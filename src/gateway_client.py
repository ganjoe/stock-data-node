"""
gateway_client.py — T-002
Manages the ib_insync connection to an IB Gateway instance. (F-CON-010, F-CON-020, F-SYS-030)
"""
from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date as date_type
from datetime import datetime, timezone
import random
from typing import Optional

from ib_insync import IB, Contract, util, ContractDescription

from models import (
    BatchConfig,
    GatewayConfig,
    IBKRContract,
    IConfigLoader,
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

    def __init__(self, config: IConfigLoader) -> None:
        self._config = config
        self._ib = IB()
        self._market_data_type: MarketDataType = MarketDataType.DELAYED
        
        # Connection suspension logic (F-ERR-070)
        self._connection_active = asyncio.Event()
        self._connection_active.set()
        self._ib.errorEvent += self._global_error_handler

    def _global_error_handler(self, reqId: int, errorCode: int, errorString: str, contract: object) -> None:
        if errorCode == 1100:
            if self._connection_active.is_set():
                logger.warning("⚠️ Connection between Gateway and IBKR lost (Error 1100). Suspending API queries...")
                self._connection_active.clear()
        elif errorCode == 1102:
            if not self._connection_active.is_set():
                logger.info("✅ Connection between Gateway and IBKR restored (Error 1102). Resuming API queries...")
                self._connection_active.set()

    async def connect(self) -> None:
        """Connects to the active IB Gateway endpoint. Raises ConnectionError on failure."""
        gw_config = self._config.get_gateway_config()
        settings = self._config.get_settings_config()
        endpoint = gw_config.active_endpoint
        logger.info(
            "🌐 Connecting to IB Gateway at %s:%d (mode=%s)…",
            endpoint.host, endpoint.port, gw_config.mode
        )
        try:
            client_id = random.randint(1, 9999)
            await self._ib.connectAsync(
                host=endpoint.host,
                port=endpoint.port,
                clientId=client_id,
                timeout=settings.gateway_connect_timeout,
            )
            logger.info(
                "✅ Connected to IB Gateway at %s:%d (clientId=%d)",
                endpoint.host, endpoint.port, client_id
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

    async def detect_market_data_type(self) -> BatchConfig:
        """
        Requests Live market data. IBKR automatically falls back to Delayed
        if no live subscription exists. We detect which one is active
        and return an adaptive BatchConfig. (F-CON-020, F-IMP-010/020)
        """
        settings = self._config.get_settings_config()
        logger.info("── Detecting market data type…")
        # Request Live (IBKR falls back to Delayed automatically)
        self._ib.reqMarketDataType(MarketDataType.LIVE)
        await asyncio.sleep(settings.market_data_type_wait)

        # Try requesting a small snapshot (SPY is highly liquid, always available)
        spy = Contract(symbol="SPY", secType="STK", exchange="SMART", currency="USD")
        try:
            ticker = self._ib.reqMktData(spy, snapshot=True)
            await asyncio.sleep(settings.market_data_snapshot_wait)

            # If we got a live last price, we have a live subscription
            if ticker.last and ticker.last > 0:
                self._market_data_type = MarketDataType.LIVE
                logger.info("✅ Market data type: LIVE (real-time)")
                return BatchConfig(
                    market_data_type=MarketDataType.LIVE,
                    max_concurrent=settings.live_max_concurrent,
                    base_pacing_delay=settings.live_pacing_delay,
                    description="Live (real-time)",
                )
            else:
                self._market_data_type = MarketDataType.DELAYED
                logger.info("ℹ️  Market data type: DELAYED (15-min delay, no live subscription)")
        except Exception as exc:
            logger.warning("Could not detect market data type: %s — assuming DELAYED", exc)
            self._market_data_type = MarketDataType.DELAYED

        return BatchConfig(
            market_data_type=MarketDataType.DELAYED,
            max_concurrent=settings.delayed_max_concurrent,
            base_pacing_delay=settings.delayed_pacing_delay,
            description="Delayed (15-min)",
        )

    async def qualify_contracts_batch(
        self, contracts: dict[str, IBKRContract]
    ) -> dict[str, IBKRContract]:
        """
        Qualifies all contracts in a single IBKR batch call.
        Returns only successfully qualified contracts (conId != 0). (F-IMP-040)
        """
        if not self._connection_active.is_set():
            logger.debug("Waiting for IBKR connection to be restored before qualifying contracts...")
            await self._connection_active.wait()
        if not contracts:
            return {}

        # Build ib_insync Contract objects
        ib_contracts: dict[str, Contract] = {}
        for ticker, ibkr in contracts.items():
            c = Contract(
                symbol=ibkr.symbol,
                secType=ibkr.sec_type,
                exchange=ibkr.exchange,
                currency=ibkr.currency,
            )
            ib_contracts[ticker] = c

        # Batch qualify
        try:
            await self._ib.qualifyContractsAsync(*ib_contracts.values())
        except Exception as exc:
            logger.warning("Batch contract qualification error: %s", exc)
            return {}

        # Filter: only successful (conId != 0)
        qualified: dict[str, IBKRContract] = {}
        for ticker, c in ib_contracts.items():
            if c.conId and c.conId != 0:
                qualified[ticker] = contracts[ticker]
            else:
                logger.warning("Contract qualification failed for %s — will be skipped", ticker)

        logger.info(
            "Contract qualification: %d/%d succeeded",
            len(qualified), len(contracts),
        )
        return qualified

    async def search_contract(self, symbol: str) -> list[ContractDescription]:
        """
        Wraps reqMatchingSymbolsAsync to find contract alternatives. (F-EXT-040)
        """
        if not symbol:
            return []
            
        if not self._connection_active.is_set():
            logger.debug("Waiting for IBKR connection to be restored before searching contract...")
            await self._connection_active.wait()
        logger.info("Searching IBKR for matching symbols: %s", symbol)
        try:
            results = await self._ib.reqMatchingSymbolsAsync(symbol)
            logger.debug("Found %d matching symbols for %s", len(results), symbol)
            return results
        except Exception as exc:
            logger.warning("Error searching matching symbols for %s: %s", symbol, exc)
            return []

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
        readable_end = end_dt.strftime("%d.%m.%Y %H:%M:%S")

        # Calculate duration string from the time window
        duration_seconds = end_ts - start_ts
        duration_str = self._seconds_to_duration(duration_seconds, timeframe)

        logger.debug(
            "Requesting historical bars: %s / %s | end=%s | duration=%s | barSize=%s",
            contract.symbol, timeframe, readable_end, duration_str, bar_size
        )

        if not self._connection_active.is_set():
            logger.debug("Waiting for IBKR connection to be restored before historical data request...")
            await self._connection_active.wait()

        captured_error: str | None = None

        def on_error(reqId: int, errorCode: int, errorString: str, contract: object) -> None:
            nonlocal captured_error
            # 162: Historical Data Error, 200: No security definition, 321: API request invalid, 10090: Pacing
            if errorCode in (162, 200, 321, 10090):
                captured_error = f"Error {errorCode}: {errorString}"

        self._ib.errorEvent += on_error
        
        try:
            bars = await self._ib.reqHistoricalDataAsync(
                contract=ib_contract,
                endDateTime=end_str,
                durationStr=duration_str,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,   # Regular Trading Hours only
                formatDate=1,  # String dates — more compatible across bar sizes
            )
        finally:
            self._ib.errorEvent -= on_error

        if captured_error:
            logger.debug("Captured IB error during download: %s", captured_error)
            raise RuntimeError(captured_error)

        if not bars:
            logger.debug("No bars returned for %s/%s", contract.symbol, timeframe)
            return []

        result: list[OHLCVBar] = []
        for bar in bars:
            ts = self._bar_date_to_timestamp(bar.date)
            if ts is None:
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
    def _bar_date_to_timestamp(bar_date: object) -> int | None:
        """
        Converts ib_insync bar.date to a Unix timestamp.
        IBKR returns different types depending on bar size and formatDate setting:
          - formatDate=1+daily  → datetime.date (e.g. date(2024, 3, 1))
          - formatDate=1+intra  → datetime (e.g. datetime(2024, 3, 1, 9, 30))
          - formatDate=2        → int (Unix timestamp, but broken for daily on some versions)
          - str                 → "20240301" or "20240301 09:30:00"
        """
        try:
            if isinstance(bar_date, datetime):
                return int(bar_date.replace(tzinfo=timezone.utc).timestamp())
            if isinstance(bar_date, date_type):
                # datetime.date — treat as midnight UTC
                return calendar.timegm(bar_date.timetuple())
            if isinstance(bar_date, int):
                return bar_date
            if isinstance(bar_date, str):
                s = bar_date.strip()
                if len(s) == 8:  # "20240301"
                    d = datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
                    return int(d.timestamp())
                # "20240301 09:30:00" or "20240301 09:30:00 US/Eastern"
                d = datetime.strptime(s[:17], "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
                return int(d.timestamp())
            return None
        except Exception:
            return None

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
