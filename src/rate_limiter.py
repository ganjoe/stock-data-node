"""
rate_limiter.py — T-009 + T-IMP-001
Adaptive rate limiting with exponential backoff and configurable concurrency.
(F-FNC-050, F-IMP-010, F-IMP-020)
"""
from __future__ import annotations

import asyncio
import logging
import time

from models import BatchConfig, IRateLimiter

logger = logging.getLogger(__name__)


class AdaptiveRateLimiter(IRateLimiter):
    """
    Rate limiter respecting IBKR pacing rules:
    - Adaptive concurrency via semaphore (Live=20, Delayed=1)
    - Adaptive base pacing (Live=0.1s, Delayed=3.0s)
    - Exponential backoff on pacing violations, up to 5 min max
    - Auto-reset on successful requests
    - Safe defaults until configure() is called: 10s / 1 concurrent
    """

    DEFAULT_DELAY = 10.0    # safe default before configure() is called
    MAX_BACKOFF = 300.0     # 5 min maximum backoff
    BACKOFF_FACTOR = 2.0

    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._base_delay: float = self.DEFAULT_DELAY
        self._current_delay: float = self.DEFAULT_DELAY
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)

    def configure(self, config: BatchConfig) -> None:
        """Applies BatchConfig pacing and concurrency settings. (F-IMP-020)"""
        self._base_delay = config.base_pacing_delay
        self._current_delay = config.base_pacing_delay
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        logger.info(
            "Rate limiter configured: pacing=%.1fs, concurrent=%d (%s)",
            config.base_pacing_delay, config.max_concurrent, config.description,
        )

    async def acquire(self) -> None:
        """Acquires semaphore slot, then waits pacing delay."""
        async with self._semaphore:
            elapsed = time.monotonic() - self._last_request_time
            wait = self._current_delay - elapsed
            if wait > 0:
                logger.debug("Rate limiter: waiting %.1fs before next request", wait)
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()

    def report_pacing_error(self) -> None:
        """Doubles the delay on a pacing error (exponential backoff)."""
        previous = self._current_delay
        self._current_delay = min(self._current_delay * self.BACKOFF_FACTOR, self.MAX_BACKOFF)
        logger.warning(
            "Pacing error — backoff increased: %.1fs → %.1fs",
            previous, self._current_delay,
        )

    def report_success(self) -> None:
        """Resets delay back to base (not hardcoded) after a successful request."""
        if self._current_delay != self._base_delay:
            logger.info(
                "Request succeeded — resetting backoff %.1fs → %.1fs",
                self._current_delay, self._base_delay,
            )
        self._current_delay = self._base_delay
