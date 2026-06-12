import asyncio
import logging
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from typing import Optional

from models import OHLCVBar

logger = logging.getLogger(__name__)

# Mapping from IBKR Ticker to YFinance Ticker
# SYSTEM is deliberately mapped to "SKIP" as it's an artifact
YF_MAPPING = {
    "LPK": "LPK.DE",
    "HPS.A": "HPS-A.TO",
    "SOI": "SOI.PA",
    "XFAB": "XFAB.PA",
    "SIVE": "SIVE.ST",
    "4GLD": "4GLD.DE",
    "SYSTEM": "SKIP",
    "NOD": "NOD.OL",
    "KCLI.CN": "KCLI.CN",
    "VLX.L": "VLX.L",
    "VLXGF": "VLXGF",
    "AMD": "AMD"
}

class YFinanceClient:
    """Client for fetching data from Yahoo Finance as a fallback."""

    @staticmethod
    def get_yf_ticker(ibkr_ticker: str) -> Optional[str]:
        return YF_MAPPING.get(ibkr_ticker, None)

    async def fetch_historical_bars(self, yf_ticker: str, start_ts: Optional[int] = None) -> list[OHLCVBar]:
        """Fetches history for the given YF ticker. If start_ts is provided, does a delta download."""
        if yf_ticker == "SKIP":
            return []
            
        try:
            logger.info("Fetching historical data from YFinance for %s...", yf_ticker)
            ticker_obj = yf.Ticker(yf_ticker)
            
            if start_ts:
                start_date = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d')
                df = await asyncio.to_thread(ticker_obj.history, start=start_date)
            else:
                # period="max" fetches all available daily data
                df = await asyncio.to_thread(ticker_obj.history, period="max")
            
            if df.empty:
                logger.warning("YFinance returned empty DataFrame for %s", yf_ticker)
                return []
                
            bars = []
            for index, row in df.iterrows():
                # index is a pandas Timestamp
                ts = int(index.timestamp())
                
                open_val = float(row["Open"])
                high_val = float(row["High"])
                low_val = float(row["Low"])
                close_val = float(row["Close"])
                vol_val = float(row["Volume"])

                # Some YF data might have NaNs, filter them
                if pd.isna(open_val) or pd.isna(close_val):
                    continue

                bars.append(OHLCVBar(
                    timestamp=ts,
                    open=open_val,
                    high=high_val,
                    low=low_val,
                    close=close_val,
                    volume=vol_val
                ))
                
            logger.info("✅ YFinance returned %d bars for %s", len(bars), yf_ticker)
            return bars
        except Exception as e:
            logger.error("❌ YFinance failed for %s: %s", yf_ticker, e)
            return []
