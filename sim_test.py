import sys
sys.path.append('src')
from market_clock import MarketClock
import datetime
import pytz

cet = pytz.timezone('Europe/Berlin')

# Sim 20:15
dt1 = cet.localize(datetime.datetime(2026, 3, 11, 20, 15, 0))
print("At 20:15 CET (Berlin Time):")
print("  SMART (US):", MarketClock.get_latest_completed_trading_day("SMART", dt1))
print("  XETRA (DE):", MarketClock.get_latest_completed_trading_day("XETRA", dt1))

# Sim 22:01
dt2 = cet.localize(datetime.datetime(2026, 3, 11, 22, 1, 0))
print("\nAt 22:01 CET (Berlin Time):")
print("  SMART (US):", MarketClock.get_latest_completed_trading_day("SMART", dt2))
print("  XETRA (DE):", MarketClock.get_latest_completed_trading_day("XETRA", dt2))
