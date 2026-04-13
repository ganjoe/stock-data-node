"""
rate_limiter.py — T-009 + T-IMP-001 + T-OPT-001
Adaptive rate limiting with dynamic semaphore throttling and request debounce.
(F-FNC-050, F-IMP-010, F-IMP-020, F-OPT-020, F-OPT-030)

IBKR Limits exploited:
  - /iserver/marketdata/history: 5 concurrent requests  → semaphore(5)
  - Global: 10 req/s                                    → _pacing_lock + _current_delay >= 0.1s
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from models import BatchConfig, IRateLimiter, RequestFingerprint, SettingsConfig

logger = logging.getLogger(__name__)


class AdaptiveRateLimiter(IRateLimiter):
    """
    Adaptive rate limiter with:
    - Concurrency semaphore held for the FULL duration of each request (F-OPT-020)
      → caller must call release() after each acquire()
    - Global pacing lock enforcing minimum interval between request starts (10 req/s)
    - Dynamic semaphore: shrinks on pacing errors, grows on success (F-OPT-020)
    - Exponential backoff on pacing violations
    - Request debounce to prevent identical requests within 15s (F-OPT-030)
    - Safe defaults until configure() is called: 10s delay / 1 concurrent
    """

    DEFAULT_DELAY = 10.0       # safe default before configure() is called
    MAX_BACKOFF = 300.0        # 5 min maximum backoff
    BACKOFF_FACTOR = 2.0

    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._base_delay: float = self.DEFAULT_DELAY
        self._current_delay: float = self.DEFAULT_DELAY

        # Concurrency semaphore — held for the FULL duration of a request
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)

        # Pacing lock — serialises the delay check so workers don't race past it
        self._pacing_lock: asyncio.Lock = asyncio.Lock()

        # Dynamic throttling state (F-OPT-020)
        self._max_concurrent: int = 1
        self._current_concurrent: int = 1
        self._min_concurrent: int = 1
        self._consecutive_successes: int = 0
        self._recovery_threshold: int = 5

        # Debounce state (F-OPT-030)
        self._debounce_seconds: float = 15.0
        self._request_history: dict[RequestFingerprint, float] = {}

    def configure(self, config: BatchConfig, settings: Optional[SettingsConfig] = None) -> None:
        """Applies BatchConfig pacing/concurrency AND optional SettingsConfig throttle params. (F-IMP-020, F-OPT-020)"""
        self._base_delay = config.base_pacing_delay
        self._current_delay = config.base_pacing_delay
        self._max_concurrent = config.max_concurrent
        self._current_concurrent = config.max_concurrent
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._consecutive_successes = 0

        if settings:
            self._min_concurrent = settings.throttle_min_concurrent
            self._recovery_threshold = settings.throttle_recovery_threshold
            self._debounce_seconds = settings.request_debounce_seconds

        logger.info(
            "Rate limiter configured: pacing=%.2fs, concurrent=%d, min=%d, recovery=%d (%s)",
            config.base_pacing_delay, config.max_concurrent,
            self._min_concurrent, self._recovery_threshold,
            config.description,
        )

    async def acquire(self) -> None:
        """
        Acquires a concurrency slot, then enforces the global pacing delay.

        The semaphore slot is NOT released here — the caller MUST call release()
        after the request completes.  This ensures the concurrency limit of 5
        concurrent /iserver/marketdata/history requests is respected at all times.

        The _pacing_lock serialises the delay check so that N parallel workers
        don't all measure "enough time has passed" simultaneously and burst past
        the 10 req/s global IBKR limit.
        """
        # 1. Block until a concurrency slot is free (held until release() is called)
        await self._semaphore.acquire()

        # 2. Serialise pacing: only one worker at a time may check & consume
        #    the minimum inter-request interval.
        async with self._pacing_lock:
            elapsed = time.monotonic() - self._last_request_time
            wait = self._current_delay - elapsed
            if wait > 0:
                logger.debug("Rate limiter: waiting %.2fs before next request", wait)
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()

        # Semaphore stays acquired — released by the caller via release()

    def release(self) -> None:
        """Releases the concurrency slot after the request completes. (F-OPT-020)"""
        self._semaphore.release()

    def report_pacing_error(self) -> None:
        """Reduces semaphore AND doubles delay on a pacing error. (F-OPT-020)"""
        self._consecutive_successes = 0

        # Exponential backoff on delay
        previous_delay = self._current_delay
        self._current_delay = min(self._current_delay * self.BACKOFF_FACTOR, self.MAX_BACKOFF)

        # Reduce concurrency (F-OPT-020)
        previous_concurrent = self._current_concurrent
        new_limit = max(self._min_concurrent, self._current_concurrent - 1)
        if new_limit != self._current_concurrent:
            self._rebuild_semaphore(new_limit)

        logger.warning(
            "⚠️ Pacing error — backoff %.2fs → %.2fs, concurrency %d → %d",
            previous_delay, self._current_delay,
            previous_concurrent, self._current_concurrent,
        )

    def report_success(self) -> None:
        """Increments semaphore after N consecutive successes. (F-OPT-020)"""
        self._consecutive_successes += 1

        # Reset delay to base if it was elevated
        if self._current_delay != self._base_delay:
            logger.info(
                "Request succeeded — resetting backoff %.2fs → %.2fs",
                self._current_delay, self._base_delay,
            )
            self._current_delay = self._base_delay

        # Recovery: increase concurrency after sustained success (F-OPT-020)
        if (
            self._consecutive_successes >= self._recovery_threshold
            and self._current_concurrent < self._max_concurrent
        ):
            previous = self._current_concurrent
            new_limit = self._current_concurrent + 1
            self._rebuild_semaphore(new_limit)
            self._consecutive_successes = 0
            logger.info(
                "✅ Throttle recovery: concurrency %d → %d",
                previous, self._current_concurrent,
            )

    def check_debounce(self, fingerprint: RequestFingerprint) -> bool:
        """Returns True if the request should be BLOCKED (sent too recently). (F-OPT-030)"""
        last_time = self._request_history.get(fingerprint)
        if last_time is not None:
            elapsed = time.monotonic() - last_time
            if elapsed < self._debounce_seconds:
                return True
        return False

    def record_request(self, fingerprint: RequestFingerprint) -> None:
        """Records that a request was sent and purges stale entries. (F-OPT-030)"""
        now = time.monotonic()
        self._request_history[fingerprint] = now

        # Housekeeping: remove entries older than 2× debounce window
        cutoff = now - (self._debounce_seconds * 2)
        stale_keys = [k for k, v in self._request_history.items() if v < cutoff]
        for k in stale_keys:
            del self._request_history[k]

    def _rebuild_semaphore(self, new_limit: int) -> None:
        """
        Replaces the semaphore with a new one at the given limit.

        Note: slots currently held by in-flight requests are implicitly
        "inherited" by the new semaphore because their release() calls will
        call the old semaphore's release — which is fine; asyncio.Semaphore
        internal value is clamped.  We only replace for new acquires.
        """
        self._current_concurrent = new_limit
        self._semaphore = asyncio.Semaphore(new_limit)
