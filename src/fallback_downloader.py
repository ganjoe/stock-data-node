import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from yfinance_client import YFinanceClient
from models import IParquetWriter, IConfigLoader

logger = logging.getLogger(__name__)

class FallbackDownloader:
    """
    Background service that scans failed_ticker.json and attempts
    to download historical data using YFinance as a fallback.
    """
    
    def __init__(self, writer: IParquetWriter, config: IConfigLoader):
        self._writer = writer
        self._config = config
        self._yf_client = YFinanceClient()
        self._running = False
        
        paths = self._config.get_paths_config()
        self._state_dir = Path(paths.parquet_dir).parent.parent / "state"
        self._failed_file = self._state_dir / "failed_ticker.json"
        self._yf_failed_file = self._state_dir / "yfinance_failed.json"
        
    def _load_yf_failed(self) -> dict:
        """Loads the YF failed trackers."""
        if not self._yf_failed_file.exists():
            return {}
        try:
            with open(self._yf_failed_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load yfinance_failed.json: %s", e)
            return {}
            
    def _save_yf_failed(self, data: dict) -> None:
        """Saves the YF failed trackers."""
        try:
            with open(self._yf_failed_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to save yfinance_failed.json: %s", e)

    def _load_failed_tickers(self) -> list[dict]:
        if not self._failed_file.exists():
            return []
        try:
            with open(self._failed_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load failed_ticker.json: %s", e)
            return []

    async def run_loop(self) -> None:
        self._running = True
        logger.info("✅ FallbackDownloader (YFinance) started.")
        
        while self._running:
            try:
                await self._process_cycle()
            except Exception as e:
                logger.error("Error in FallbackDownloader cycle: %s", e, exc_info=True)
                
            # Wait 6 hours between full sweeps
            for _ in range(6 * 3600):
                if not self._running:
                    break
                await asyncio.sleep(1)
                
        logger.info("🛑 FallbackDownloader stopped.")
        
    def stop(self) -> None:
        self._running = False

    async def _process_cycle(self) -> None:
        failed_list = self._load_failed_tickers()
        if not failed_list:
            return
            
        yf_failed = self._load_yf_failed()
        
        for entry in failed_list:
            if not self._running:
                break
                
            ticker = entry.get("ticker")
            if not ticker:
                continue
                
            # If already marked as a complete hopeless case, skip to avoid spam
            if ticker in yf_failed:
                continue
                
            # Staleness check (F-OPT-080 equivalent for YFinance)
            last_ts = self._writer.read_last_timestamp(ticker, "1D")
            if last_ts:
                now = datetime.now(timezone.utc).timestamp()
                age_days = (now - last_ts) / 86400.0
                if age_days < 0.9:
                    logger.debug("YFinance: %s is fresh (%.1f days old). Skipping.", ticker, age_days)
                    continue
                
            yf_ticker = self._yf_client.get_yf_ticker(ticker)
            if not yf_ticker:
                # No mapping exists
                logger.warning("YFinance: No mapping for failed ticker %s", ticker)
                yf_failed[ticker] = {
                    "reason": "No YFinance mapping",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                self._save_yf_failed(yf_failed)
                continue
                
            if yf_ticker == "SKIP":
                logger.info("YFinance: Skipping artifact ticker %s", ticker)
                yf_failed[ticker] = {
                    "reason": "Explicitly skipped",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                self._save_yf_failed(yf_failed)
                continue
                
            timeout = self._config.get_settings_config().yfinance_timeout
            bars = await self._yf_client.fetch_historical_bars(yf_ticker, start_ts=last_ts, timeout=timeout)
            if not bars:
                logger.error("YFinance: Download failed or empty for %s (%s)", ticker, yf_ticker)
                yf_failed[ticker] = {
                    "reason": "YFinance download failed or empty",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                self._save_yf_failed(yf_failed)
                continue
                
            # Success! Save to parquet
            try:
                self._writer.append_bars(ticker, "1D", bars)
                logger.info("✅ YFinance: Saved %d bars for %s", len(bars), ticker)
                # Note: We do NOT remove it from failed_ticker.json here.
                # If we do, IBKR will just try to download it again and fail again!
                # By leaving it in failed_ticker.json, IBKR ignores it, but YFinance handles it.
            except Exception as e:
                logger.error("YFinance: Failed to write parquet for %s: %s", ticker, e)
