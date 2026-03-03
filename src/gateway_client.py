"""
gateway_client.py — T-002
Manages the ib_insync connection to an IB Gateway instance. (F-CON-010, F-CON-020, F-SYS-030)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from ib_insync import IB, Contract, util

from models import (
    GatewayConfig,
    IBKRContract,
    IGatewayClient,
    MarketDataType,
    OHLCVBar,
)

logger = logging.getLogger(__name__)

# Maps our timeframe string → (IBKR barSizeSetting, IBKR durationStr)
TIMEFRAME_MAP: dict[str, tuple[str, str]] = {
    "1m":  ("1 min",   "1 D"),
    "5m":  ("5 mins",  "1 W"),
    "15m": ("15 mins", "2 W"),
    "30m": ("30 mins", "1 M"),
    "1h":  ("1 hour",  "1 M"),
    "4h":  ("4 hours", "3 M"),
    "1D":  ("1 day",   "1 Y"),
    "1W":  ("1 week",  "5 Y"),
    "1M":  ("1 month", "10 Y"),
}

# Maximum history lookback per timeframe (seconds)
MAX_HISTORY_LOOKBACK: dict[str, int] = {
    "1m":  86400 * 30,
    "5m":  86400 * 90,
    "15m": 86400 * 180,
    "30m": 86400 * 180,
    "1h":  86400 * 365,
    "4h":  86400 * 365 * 2,
    "1D":  86400 * 365 * 20,
    "1W":  86400 * 365 * 20,
    "1M":  86400 * 365 * 20,
}


class GatewayClient(IGatewayClient):
    """
    Wraps ib_insync.IB to provide async connect/disconnect,
    market data type detection, and historical bar downloads.
    """

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._ib = IB()
        self._market_data_type: MarketDataType = MarketDataType.DELAYED

    async def connect(self) -> None:
        """Connects to the active IB Gateway endpoint. Raises ConnectionError on failure."""
        endpoint = self._config.active_endpoint
        logger.info(
            "Connecting to IB Gateway at %s:%d (mode=%s)…",
            endpoint.host, endpoint.port, self._config.mode
        )
        try:
            await self._ib.connectAsync(
                host=endpoint.host,
                port=endpoint.port,
                clientId=1,
                timeout=20,
            )
            logger.info(
                "✅ Connected to IB Gateway at %s:%d",
                endpoint.host, endpoint.port
            )
        except Exception as exc:
            msg = (
                f"Cannot connect to IB Gateway at {endpoint.host}:{endpoint.port}. "
                f"Is it running and authenticated? Error: {exc}"
            )
            logger.error(msg)
            raise ConnectionError(msg) from exc

    async def disconnect(self) -> None:
        """Gracefully disconnects from the gateway."""
        if self._ib.isConnected():
            self._ib.disconnect()
            logger.info("Disconnected from IB Gateway.")

    def is_connected(self) -> bool:
        return self._ib.isConnected()

    async def detect_market_data_type(self) -> MarketDataType:
        """
        Requests Live market data. IBKR automatically falls back to Delayed
        if no live subscription exists. We detect which one is active.
        (F-CON-020)
        """
        logger.info("Detecting market data type…")
        # Request Live (IBKR falls back to Delayed automatically)
        self._ib.reqMarketDataType(MarketDataType.LIVE)
        await asyncio.sleep(0.5)  # brief pause for the setting to propagate

        # Try requesting a small snapshot (AAPL is highly liquid, always available)
        spy = Contract(symbol="SPY", secType="STK", exchange="SMART", currency="USD")
        try:
            ticker = self._ib.reqMktData(spy, snapshot=True)
            await asyncio.sleep(2)  # wait for data
            self._ib.cancelMktData(spy)

            # If we got a live last price, we have a live subscription
            if ticker.last and ticker.last > 0:
                self._market_data_type = MarketDataType.LIVE
                logger.info("✅ Market data type: LIVE (real-time)")
            else:
                self._market_data_type = MarketDataType.DELAYED
                logger.info("ℹ️  Market data type: DELAYED (15-min delay, no live subscription)")
        except Exception as exc:
            logger.warning("Could not detect market data type: %s — assuming DELAYED", exc)
            self._market_data_type = MarketDataType.DELAYED

        return self._market_data_type

    async def request_historical_bars(
        self,
        contract: IBKRContract,
        timeframe: str,
        start_ts: int,
        end_ts: int,
    ) -> list[OHLCVBar]:
        """
        Downloads historical bars for a given contract and time window.
        Raises ValueError for unknown timeframes, propagates IBKR errors to caller.
        """
        if timeframe not in TIMEFRAME_MAP:
            raise ValueError(
                f"Unknown timeframe '{timeframe}'. "
                f"Supported: {list(TIMEFRAME_MAP.keys())}"
            )

        bar_size, _ = TIMEFRAME_MAP[timeframe]

        # Convert IBKR contract
        ib_contract = Contract(
            symbol=contract.symbol,
            secType=contract.sec_type,
            exchange=contract.exchange,
            currency=contract.currency,
        )

        # Convert end_ts to IBKR end datetime string (YYYYMMDD HH:MM:SS UTC)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        end_str = end_dt.strftime("%Y%m%d %H:%M:%S UTC")

        # Calculate duration string from the time window
        duration_seconds = end_ts - start_ts
        duration_str = self._seconds_to_duration(duration_seconds, timeframe)

        logger.debug(
            "Requesting historical bars: %s / %s | end=%s | duration=%s | barSize=%s",
            contract.symbol, timeframe, end_str, duration_str, bar_size
        )

        bars = await self._ib.reqHistoricalDataAsync(
            contract=ib_contract,
            endDateTime=end_str,
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,   # Regular Trading Hours only
            formatDate=2,  # Unix timestamps
        )

        if not bars:
            logger.debug("No bars returned for %s/%s", contract.symbol, timeframe)
            return []

        result: list[OHLCVBar] = []
        for bar in bars:
            ts = int(bar.date.timestamp()) if hasattr(bar.date, "timestamp") else int(bar.date)
            # Filter to the requested window
            if ts < start_ts or ts > end_ts:
                continue
            result.append(OHLCVBar(
                timestamp=ts,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            ))

        logger.debug("Received %d bars for %s/%s", len(result), contract.symbol, timeframe)
        return result

    @staticmethod
    def _seconds_to_duration(seconds: int, timeframe: str) -> str:
        """Converts a time window in seconds to IBKR's durationStr format."""
        if timeframe in ("1M",):
            years = max(1, seconds // (86400 * 365))
            return f"{years} Y"
        if timeframe in ("1W", "1D", "4h"):
            years = seconds // (86400 * 365)
            if years >= 1:
                return f"{years} Y"
            months = seconds // (86400 * 30)
            if months >= 1:
                return f"{months} M"
        days = max(1, seconds // 86400)
        if days >= 365:
            return f"{days // 365} Y"
        if days >= 30:
            return f"{days // 30} M"
        if days >= 7:
            return f"{days // 7} W"
        return f"{days} D"
