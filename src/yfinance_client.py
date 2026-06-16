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
    
    _dynamic_mapping = {}

    @classmethod
    async def resolve_ticker(cls, ibkr_ticker: str) -> str:
        if ibkr_ticker in YF_MAPPING:
            return YF_MAPPING[ibkr_ticker]
        if ibkr_ticker in cls._dynamic_mapping:
            return cls._dynamic_mapping[ibkr_ticker]
            
        def _search():
            try:
                s = yf.Search(ibkr_ticker, max_results=1)
                if hasattr(s, 'quotes') and s.quotes:
                    return s.quotes[0].get("symbol")
            except Exception as e:
                logger.error("yf.Search failed for %s: %s", ibkr_ticker, e)
            return None
            
        logger.info("YF Ticker not in static mapping, searching YFinance for %s...", ibkr_ticker)
        result = await asyncio.to_thread(_search)
        
        if result:
            logger.info("✅ YFinance search mapped %s to %s", ibkr_ticker, result)
            cls._dynamic_mapping[ibkr_ticker] = result
            return result
            
        return ibkr_ticker

    @staticmethod
    async def check_availability(yf_ticker: str) -> bool:
        if yf_ticker == "SKIP":
            return False
        try:
            ticker_obj = yf.Ticker(yf_ticker)
            # Fetching info can be slow, run in thread
            info = await asyncio.to_thread(lambda: ticker_obj.info)
            # If info dict is not empty and has typical fields, it exists
            return bool(info and "regularMarketPrice" in info or "symbol" in info)
        except Exception as e:
            logger.error("YFinance check_availability failed for %s: %s", yf_ticker, e)
            return False

    async def fetch_historical_bars(self, yf_ticker: str, start_ts: Optional[int] = None, timeout: float = 10.0) -> list[OHLCVBar]:
        """Fetches history for the given YF ticker. If start_ts is provided, does a delta download."""
        if yf_ticker == "SKIP":
            return []
            
        try:
            logger.info("Fetching historical data from YFinance for %s...", yf_ticker)
            ticker_obj = yf.Ticker(yf_ticker)
            
            if start_ts:
                start_date = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d')
                df = await asyncio.to_thread(ticker_obj.history, start=start_date, timeout=timeout)
            else:
                # period="max" fetches all available daily data
                df = await asyncio.to_thread(ticker_obj.history, period="max", timeout=timeout)
            
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
