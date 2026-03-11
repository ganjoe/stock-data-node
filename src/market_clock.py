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

    # US Holidays (NYSE/NASDAQ) (2024-2027)
    US_HOLIDAYS: set[date] = {
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

    # German Holidays (XETRA, IBIS, FWB) (2024-2027)
    GERMAN_HOLIDAYS: set[date] = {
        # 2024
        date(2024, 1, 1), date(2024, 3, 29), date(2024, 4, 1), 
        date(2024, 5, 1), date(2024, 12, 24), date(2024, 12, 25), 
        date(2024, 12, 26), date(2024, 12, 31),
        # 2025
        date(2025, 1, 1), date(2025, 4, 18), date(2025, 4, 21), 
        date(2025, 5, 1), date(2025, 12, 24), date(2025, 12, 25), 
        date(2025, 12, 26), date(2025, 12, 31),
        # 2026
        date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), 
        date(2026, 5, 1), date(2026, 12, 24), date(2026, 12, 25), 
        date(2026, 12, 26), date(2026, 12, 31),
        # 2027
        date(2027, 1, 1), date(2027, 3, 26), date(2027, 3, 29), 
        date(2027, 5, 1), date(2027, 12, 24), date(2027, 12, 25), 
        date(2027, 12, 26), date(2027, 12, 31),
    }

    # UK Holidays (LSE) (2024-2027)
    UK_HOLIDAYS: set[date] = {
        # 2024
        date(2024, 1, 1), date(2024, 3, 29), date(2024, 4, 1),
        date(2024, 5, 6), date(2024, 5, 27), date(2024, 8, 26),
        date(2024, 12, 25), date(2024, 12, 26),
        # 2025
        date(2025, 1, 1), date(2025, 4, 18), date(2025, 4, 21),
        date(2025, 5, 5), date(2025, 5, 26), date(2025, 8, 25),
        date(2025, 12, 24), date(2025, 12, 25), date(2025, 12, 26), date(2025, 12, 31),
        # 2026
        date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6),
        date(2026, 5, 4), date(2026, 5, 25), date(2026, 8, 31),
        date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 28),
        # 2027
        date(2027, 1, 1), date(2027, 3, 26), date(2027, 3, 29),
        date(2027, 5, 3), date(2027, 5, 31), date(2027, 8, 30),
        date(2027, 12, 24), date(2027, 12, 27), date(2027, 12, 28), date(2027, 12, 31),
    }

    HOLIDAYS = US_HOLIDAYS  # backwards compatibility for get_status

    MAX_HOLIDAY_YEAR = 2027

    @staticmethod
    def _get_exchange_config(exchange: str) -> tuple[pytz.BaseTzInfo, time, time, set[date]]:
        """Returns (timezone, open_time, close_time, holidays) for an exchange."""
        import logging
        logger = logging.getLogger(__name__)

        exchange = exchange.upper().strip()
        
        # US Markets
        if exchange in {"SMART", "ISLAND", "NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", ""}:
            return pytz.timezone("US/Eastern"), time(9, 30), time(16, 0), MarketClock.US_HOLIDAYS
        
        # German Markets
        if exchange in {"XETRA", "IBIS", "FWB"}:
            return pytz.timezone("Europe/Berlin"), time(9, 0), time(17, 30), MarketClock.GERMAN_HOLIDAYS
            
        # UK Markets
        if exchange in {"LSE"}:
            return pytz.timezone("Europe/London"), time(8, 0), time(16, 30), MarketClock.UK_HOLIDAYS

        # Fallback for unknown
        logger.warning("⚠️ Unknown exchange '%s' — using 24/7 UTC fallback for MarketClock", exchange)
        return pytz.timezone("UTC"), time(0, 0), time(23, 59, 59), set()

    @staticmethod
    def get_latest_completed_trading_day(exchange: str, current_dt: datetime | None = None) -> date:
        """
        Returns the most recently fully completed trading day (date) for the given exchange.
        (F-IMP-110)
        """
        tz, _, close_time, holidays = MarketClock._get_exchange_config(exchange)
        
        if current_dt is None:
            current_dt = datetime.now(tz)
        elif current_dt.tzinfo is None:
            current_dt = tz.localize(current_dt)
        else:
            current_dt = current_dt.astimezone(tz)

        candidate_date = current_dt.date()
        current_time = current_dt.time()

        # If today is a trading day and market has already closed, today is fully completed
        if candidate_date.weekday() < 5 and candidate_date not in holidays:
            if current_time >= close_time:
                return candidate_date

        # Otherwise, search backwards for the most recent completed trading day
        # Limit search to 30 days to avoid infinite loops
        candidate_dt = current_dt - timedelta(days=1)
        for _ in range(30):
            c_date = candidate_dt.date()
            if c_date.weekday() < 5 and c_date not in holidays:
                return c_date
            candidate_dt -= timedelta(days=1)
            
        return (current_dt - timedelta(days=1)).date()  # Fallback

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
