from datetime import datetime as dt, time as dt_time, timezone
import pytz

now_berlin = dt.now(pytz.timezone("Europe/Berlin"))
now_est = dt.now(pytz.timezone("US/Eastern"))
print(f"Berlin: {now_berlin}")
print(f"US/Eastern: {now_est}")
