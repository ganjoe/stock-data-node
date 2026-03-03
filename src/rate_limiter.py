"""
rate_limiter.py — T-009
Adaptive rate limiting with exponential backoff. (F-FNC-050)
"""
from __future__ import annotations

import asyncio
import logging
import time

from models import IRateLimiter

logger = logging.getLogger(__name__)


class AdaptiveRateLimiter(IRateLimiter):
    """
    Rate limiter respecting IBKR pacing rules:
    - Safe default: 10 seconds between requests
    - Exponential backoff on pacing violations, up to 5 min max
    - Auto-reset on successful requests
    """

    BASE_DELAY = 10.0       # seconds between requests (safe default for IBKR)
    MAX_BACKOFF = 300.0     # 5 min maximum backoff
    BACKOFF_FACTOR = 2.0

    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._current_delay: float = self.BASE_DELAY

    async def acquire(self) -> None:
        """Waits until the required delay has elapsed since the last request."""
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
            previous, self._current_delay
        )

    def report_success(self) -> None:
        """Resets delay back to baseline after a successful request."""
        if self._current_delay != self.BASE_DELAY:
            logger.info(
                "Request succeeded — resetting backoff %.1fs → %.1fs",
                self._current_delay, self.BASE_DELAY
            )
        self._current_delay = self.BASE_DELAY
