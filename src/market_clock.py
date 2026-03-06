"""
market_clock.py — T-IMP-007
NYSE market hours awareness with hardcoded holidays. (F-IMP-080)
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytz

from models import MarketStatus


class MarketClock:
    """
    Simple model for NYSE trading hours including holidays (2024-2027).
    All times are US/Eastern.
    """

    TIMEZONE = pytz.timezone("US/Eastern")
    OPEN_TIME = time(9, 30)
    CLOSE_TIME = time(16, 0)

    # NYSE Holidays (hardcoded for robustness, 2024-2027)
    HOLIDAYS: set[date] = {
        # 2024
        date(2024, 1, 1),    # New Year's Day
        date(2024, 1, 15),   # MLK Day
        date(2024, 2, 19),   # Washington's Birthday
        date(2024, 3, 29),   # Good Friday
        date(2024, 5, 27),   # Memorial Day
        date(2024, 6, 19),   # Juneteenth
        date(2024, 7, 4),    # Independence Day
        date(2024, 9, 2),    # Labor Day
        date(2024, 11, 28),  # Thanksgiving Day
        date(2024, 12, 25),  # Christmas Day
        # 2025
        date(2025, 1, 1),    # New Year's Day
        date(2025, 1, 20),   # MLK Day
        date(2025, 2, 17),   # Washington's Birthday
        date(2025, 4, 18),   # Good Friday
        date(2025, 5, 26),   # Memorial Day
        date(2025, 6, 19),   # Juneteenth
        date(2025, 7, 4),    # Independence Day
        date(2025, 9, 1),    # Labor Day
        date(2025, 11, 27),  # Thanksgiving Day
        date(2025, 12, 25),  # Christmas Day
        # 2026
        date(2026, 1, 1),    # New Year's Day
        date(2026, 1, 19),   # MLK Day
        date(2026, 2, 16),   # Washington's Birthday
        date(2026, 4, 3),    # Good Friday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day (observed)
        date(2026, 9, 7),    # Labor Day
        date(2026, 11, 26),  # Thanksgiving Day
        date(2026, 12, 25),  # Christmas Day
        # 2027
        date(2027, 1, 1),    # New Year's Day
        date(2027, 1, 18),   # MLK Day
        date(2027, 2, 15),   # Washington's Birthday
        date(2027, 3, 26),   # Good Friday
        date(2027, 5, 31),   # Memorial Day
        date(2027, 6, 18),   # Juneteenth (observed)
        date(2027, 7, 5),    # Independence Day (observed)
        date(2027, 9, 6),    # Labor Day
        date(2027, 11, 25),  # Thanksgiving Day
        date(2027, 12, 24),  # Christmas Day (observed)
    }

    MAX_HOLIDAY_YEAR = 2027

    @staticmethod
    def get_status() -> dict:
        """
        Returns current NYSE market status with timing information.

        Keys: status, server_time_et, next_event, seconds_to_next_event,
              human_readable, calendar_outdated
        """
        now = datetime.now(MarketClock.TIMEZONE)
        today_date = now.date()
        current_time = now.time()

        status = MarketStatus.CLOSED
        next_event = "OPEN"
        time_to_event = timedelta(0)

        # 1. Holiday check
        if today_date in MarketClock.HOLIDAYS:
            status = MarketStatus.CLOSED_HOLIDAY
            next_open = MarketClock._get_next_open(now)
            time_to_event = next_open - now

        # 2. Weekend check
        elif today_date.weekday() >= 5:
            status = MarketStatus.CLOSED_WEEKEND
            next_open = MarketClock._get_next_open(now)
            time_to_event = next_open - now

        # 3. Weekday logic
        else:
            if current_time < MarketClock.OPEN_TIME:
                status = MarketStatus.PRE_MARKET
                today_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
                time_to_event = today_open - now

            elif MarketClock.OPEN_TIME <= current_time < MarketClock.CLOSE_TIME:
                status = MarketStatus.OPEN
                next_event = "CLOSE"
                today_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
                time_to_event = today_close - now

            else:
                status = MarketStatus.POST_MARKET
                next_open = MarketClock._get_next_open(now)
                time_to_event = next_open - now

        # Calendar staleness warning
        calendar_outdated = now.year > MarketClock.MAX_HOLIDAY_YEAR

        return {
            "status": status.value,
            "server_time_et": now.strftime("%d.%m.%Y %H:%M:%S %Z"),
            "next_event": next_event,
            "seconds_to_next_event": int(time_to_event.total_seconds()),
            "human_readable": str(time_to_event).split(".")[0],
            "calendar_outdated": calendar_outdated,
        }

    @staticmethod
    def _get_next_open(current_dt: datetime) -> datetime:
        """Finds the next valid trading day open (9:30 AM ET)."""
        candidate = current_dt + timedelta(days=1)
        while True:
            if candidate.weekday() >= 5:
                candidate += timedelta(days=1)
                continue
            if candidate.date() in MarketClock.HOLIDAYS:
                candidate += timedelta(days=1)
                continue
            return candidate.replace(hour=9, minute=30, second=0, microsecond=0)
