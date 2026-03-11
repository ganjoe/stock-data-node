import time
from datetime import datetime as dt, time as dt_time
import pytz
from market_clock import MarketClock

exchange = "NASDAQ"
print("Current UTC:", dt.now(pytz.UTC))
result = MarketClock.get_latest_completed_trading_day(exchange)
tz, open_time, close_time, holidays = MarketClock._get_exchange_config(exchange)
current_tz = dt.now(tz)
print("Exchange TZ:", tz)
print("Exchange current time:", current_tz)
print("Exchange close time:", close_time)
print("Latest completed:", result)
print("Is candidate closed?", current_tz.time() >= close_time)

