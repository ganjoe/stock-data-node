"""
staleness.py — T-IMP-008
Timeframe-aware staleness check for cached market data. (F-IMP-090)
"""
from __future__ import annotations

from datetime import datetime, time


# Staleness thresholds in seconds, per timeframe
_STALENESS_THRESHOLDS: dict[str, int | None] = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "30m": 1800,
    "1h":  3600,
    "4h":  14400,
    "1D":  None,    # special: date-based comparison
    "1W":  7 * 86400,
    "1M":  30 * 86400,
}


def is_stale(last_timestamp: int, timeframe: str) -> bool:
    """
    Checks if cached data is stale given the last bar's unix timestamp.

    - Daily (1D):   stale if last bar's date < today midnight
    - Intraday:     stale if elapsed time > threshold for that timeframe
    - Weekly/Monthly: stale if elapsed > 7d / 30d
    - Unknown:      always stale (safety default)
    """
    now = datetime.now()
    last_dt = datetime.fromtimestamp(last_timestamp)

    # Daily: date-based check
    if timeframe == "1D":
        today_midnight = datetime.combine(now.date(), time.min)
        return last_dt < today_midnight

    # Lookup threshold
    threshold = _STALENESS_THRESHOLDS.get(timeframe)
    if threshold is None:
        # Unknown timeframe → assume stale for safety
        return True

    elapsed = (now - last_dt).total_seconds()
    return elapsed > threshold
