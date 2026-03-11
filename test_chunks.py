import time
from datetime import datetime as dt, time as dt_time
import pytz
from market_clock import MarketClock

exchange = "NYSE"
now = int(time.time())
latest_day = MarketClock.get_latest_completed_trading_day(exchange=exchange)
tz, _, _, _ = MarketClock._get_exchange_config(exchange)
                
end_of_day_dt = tz.localize(dt.combine(latest_day, dt_time(23, 59, 59)))
effective_now = int(end_of_day_dt.timestamp())

print(f"now: {now}")
print(f"effective_now raw: {effective_now}")
effective_now = min(now, effective_now)
print(f"effective_now min: {effective_now}")

end_dt = dt.fromtimestamp(effective_now, tz=pytz.UTC)
end_str = end_dt.strftime("%Y%m%d %H:%M:%S UTC")
readable_end = end_dt.strftime("%d.%m.%Y %H:%M:%S")

print(f"end_str = {end_str}")
print(f"readable_end = {readable_end}")
