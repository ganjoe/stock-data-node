"""
api_server.py — T-008
Minimal FastAPI REST API for ticker download requests. (F-INT-010, F-CFG-050)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING, Any
from pathlib import Path

if TYPE_CHECKING:
    from file_watcher import FileWatcher

from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel



from models import (
    DownloadPriority,
    DownloadRequest,
    FailedTickerEntry,
    IConfigLoader,
    IFailedTickerStore,
    IParquetWriter,
    IPriorityQueue,
    ITickerResolver,
    IGatewayClient,
)

logger = logging.getLogger(__name__)


# ─── Pydantic Models ─────────────────────────────────────────────

class TickerRequest(BaseModel):
    ticker: str
    timeframes: Optional[list[str]] = None   # None = use per-ticker config / default


class TickerResponse(BaseModel):
    ticker: str
    timeframes: list[str]
    status: str       # "queued" | "error"
    message: str


class ProviderRequest(BaseModel):
    provider: str


class StatusResponse(BaseModel):
    queue_size: int


# ─── Helpers ─────────────────────────────────────────────────────

def get_staleness_distribution(config: IConfigLoader, resolver: ITickerResolver, writer: IParquetWriter) -> dict:
    from pathlib import Path
    import time
    
    parquet_dir = config.get_paths_config().parquet_dir
    p_dir = Path(parquet_dir)
    all_tickers = set()
    if p_dir.exists() and p_dir.is_dir():
        for item in p_dir.iterdir():
            if not item.is_dir():
                continue
            all_tickers.add(item.name)
            
    age_counts = {}
    now = time.time()
    for ticker in all_tickers:
        if resolver.is_ignored(ticker):
            continue
        timeframes = config.get_timeframes_for_ticker(ticker)
        for tf in timeframes:
            last_ts = writer.read_last_timestamp(ticker, tf)
            if last_ts is None:
                bucket = "No data"
            else:
                last_updated = float(last_ts)
                days_outdated = int((now - last_updated) // 86400)
                bucket = f"-{days_outdated} days"
            age_counts[bucket] = age_counts.get(bucket, 0) + 1
            
    return age_counts

def enqueue_staleness_sweep(
    watcher: Any, config: IConfigLoader, resolver: ITickerResolver, queue: IPriorityQueue, writer: IParquetWriter
) -> int:
    """
    Scans watch directory first, then parquet directory for all known tickers,
    checks timeframes, and enqueues them for update.
    Returns the number of enqueue requests created.
    """
    from models import DownloadRequest, DownloadPriority
    from pathlib import Path
    
    # 1. Ingest newly placed watch files
    watcher.scan_once()

    # 2. Get parquet_dir
    parquet_dir = config.get_paths_config().parquet_dir
    p_dir = Path(parquet_dir)

    all_tickers = set()
    # 3. List all ticker subdirectories
    if p_dir.exists() and p_dir.is_dir():
        for item in p_dir.iterdir():
            if not item.is_dir():
                continue
            all_tickers.add(item.name)
    
    logger.info("▶️  Triggering staleness sweep for %d ticker(s)", len(all_tickers))
    count = 0
    age_counts = {}
    import time
    now = time.time()
    
    for ticker in all_tickers:
        if resolver.is_ignored(ticker):
            continue
        
        # 4. Fetch timeframes and enqueue
        timeframes = config.get_timeframes_for_ticker(ticker)
        for tf in timeframes:
            contract = resolver.resolve(ticker)
            last_ts = writer.read_last_timestamp(ticker, tf)
            
            if last_ts is None:
                last_updated = 0.0
                bucket = "No data"
            else:
                last_updated = float(last_ts)
                days_outdated = int((now - last_updated) // 86400)
                bucket = f"-{days_outdated} days"

            age_counts[bucket] = age_counts.get(bucket, 0) + 1
            
            req = DownloadRequest(
                ticker=ticker,
                timeframe=tf,
                priority=DownloadPriority.STALENESS,
                contract=contract,
                last_updated=last_updated,
            )
            queue.enqueue(req)
            count += 1
            
    if count > 0:
        logger.info("Staleness Distribution:")
        def bucket_sort_key(b):
            return age_counts[b]
            
        for b in sorted(age_counts.keys(), key=bucket_sort_key, reverse=True):
            c = age_counts[b]
            pct = (c / count) * 100
            logger.info("  %5.1f%% %-10s (%d)", pct, b, c)

    logger.info("✅ Enqueued %d staleness requests", count)
    return count

# ─── Factory ─────────────────────────────────────────────────────

def create_api(
    queue: IPriorityQueue,
    resolver: ITickerResolver,
    config: IConfigLoader,
    failed_store: IFailedTickerStore,
    watcher: "FileWatcher",
    writer: IParquetWriter,
    gateway: IGatewayClient,
) -> FastAPI:
    """
    Creates and returns the FastAPI application with all routes configured.
    Dependency injection: all services passed explicitly (no global state).
    """
    app = FastAPI(
        title="Stock Data Node API",
        description="Request historical OHLCV downloads from IB Gateway.",
        version="1.0.0",
    )

    from fastapi import Body
    
    @app.post("/config/provider/{ticker}")
    async def set_provider(ticker: str, req: ProviderRequest = Body(...)) -> dict:
        """Sets the provider for a ticker, updates the config, and deletes existing parquet data."""
        from pathlib import Path
        import shutil
        import asyncio
        from models import IBKRContract
        
        normalized = ticker.strip().upper()
        
        config.reload_if_changed()
        mapping = config.get_ticker_map()
        contract = mapping.get(normalized)
        
        if not contract or contract.symbol == "SKIP":
            contract = IBKRContract(symbol=normalized, exchange="", currency="", sec_type="", provider=req.provider.upper())
        else:
            contract.provider = req.provider.upper()
            
        config.update_ticker_map(normalized, contract)
        
        parquet_dir = config.get_paths_config().parquet_dir
        ticker_path = Path(parquet_dir) / normalized
        deleted = False
        if ticker_path.exists() and ticker_path.is_dir():
            try:
                await asyncio.to_thread(shutil.rmtree, ticker_path)
                logger.info("Deleted old chart data for %s due to provider switch", normalized)
                deleted = True
            except Exception as e:
                logger.error("Failed to delete chart data for %s: %s", normalized, e)
                
        return {"ticker": normalized, "provider": req.provider.upper(), "data_deleted": deleted}

    @app.post("/download", response_model=TickerResponse)
    async def request_download(body: TickerRequest) -> TickerResponse:
        """
        Enqueues a ticker (and optional timeframes) for historical download.
        API-sourced requests have highest priority (Prio 1 over file-watcher Prio 2).
        """
        ticker = body.ticker.strip().upper()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker must not be empty.")

        logger.info("API: download request for %s (timeframes=%s)", ticker, body.timeframes)

        # Automatically de-blacklist if it was previously failed, allowing a retry
        if failed_store.is_blacklisted(ticker):
            logger.info("ℹ️ API request for blacklisted ticker %s — removing from blacklist to allow retry", ticker)
            failed_store.remove(ticker)
            
        # Check if mapped to SKIP
        if resolver.is_ignored(ticker):
            logger.warning("❌ Rejected API download for %s (mapped to SKIP)", ticker)
            raise HTTPException(status_code=400, detail=f"Ticker {ticker} is mapped to SKIP")
        # Resolve ticker to IBKR contract (will be None if unmapped, triggering Auto-Discovery later)
        contract = resolver.resolve(ticker)

        # Determine timeframes
        if body.timeframes:
            # Persist any new timeframes for this ticker (F-CFG-050)
            for tf in body.timeframes:
                config.add_timeframe_to_ticker(ticker, tf)
            timeframes = body.timeframes
        else:
            timeframes = config.get_timeframes_for_ticker(ticker)

        # Enqueue: daily-first ordering
        daily_tfs = [tf for tf in timeframes if tf == "1D"]
        other_tfs  = [tf for tf in timeframes if tf != "1D"]
        ordered_tfs = daily_tfs + other_tfs

        for tf in ordered_tfs:
            req = DownloadRequest(
                ticker=ticker,
                timeframe=tf,
                priority=DownloadPriority.API,
                contract=contract,
            )
            queue.enqueue(req)
            logger.info("✅ API request accepted: %s / %s", ticker, tf)


        logger.info(
            "API: enqueued %s for timeframes %s (priority=API)",
            ticker, ordered_tfs
        )

        return TickerResponse(
            ticker=ticker,
            timeframes=ordered_tfs,
            status="queued",
            message=f"Enqueued {len(ordered_tfs)} download(s) for {ticker}.",
        )

    @app.post("/trigger-staleness")
    async def trigger_staleness() -> JSONResponse:
        """
        Scans watch directory first, then parquet directory for all known tickers,
        checks timeframes, and enqueues them for update.
        Returns 202 Accepted. (F-API-040)
        """
        logger.info("API: Trigger staleness request received.")
        # Process the sweep
        count = enqueue_staleness_sweep(watcher, config, resolver, queue, writer)

        # 5. Return JSONResponse with 202
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": "accepted", "tickers_evaluated": count}
        )


    @app.get("/staleness/report")
    async def get_staleness_report() -> dict:
        """Returns the current staleness distribution without triggering a sweep."""
        return get_staleness_distribution(config, resolver, writer)

    @app.get("/fallback/check/{ticker}")
    async def check_yfinance(ticker: str) -> dict:
        """Runs a quick check if a ticker exists in YFinance."""
        from yfinance_client import YFinanceClient
        yf_ticker = await YFinanceClient.resolve_ticker(ticker.upper())
        exists = await YFinanceClient.check_availability(yf_ticker)
        return {"ticker": ticker.upper(), "yf_ticker": yf_ticker, "yfinance_available": exists}

    @app.get("/data/status/{ticker}")
    async def get_data_status(ticker: str) -> dict:
        """Returns whether the parquet folder exists, if it has data, and the last candle info."""
        from pathlib import Path
        from datetime import datetime, timezone
        
        ticker = ticker.upper()
        parquet_dir = config.get_paths_config().parquet_dir
        ticker_path = Path(parquet_dir) / ticker
        
        folder_exists = ticker_path.exists() and ticker_path.is_dir()
        
        tfs = config.get_timeframes_for_ticker(ticker)
        if not tfs:
            tfs = ["1D"]
            
        data_status = {}
        for tf in tfs:
            last_ts = writer.read_last_timestamp(ticker, tf)
            if last_ts is None:
                data_status[tf] = {"has_data": False}
            else:
                last_dt = datetime.fromtimestamp(float(last_ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                data_status[tf] = {
                    "has_data": True,
                    "last_candle_timestamp": float(last_ts),
                    "last_candle_date": last_dt
                }
                
        return {
            "ticker": ticker,
            "folder_exists": folder_exists,
            "timeframes": data_status
        }

    @app.get("/status/connection")
    async def get_connection_status() -> dict:
        """Returns the IBKR Gateway connection status."""
        return {"connected": gateway.is_connected()}

    @app.get("/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Returns current queue depth."""
        return StatusResponse(queue_size=queue.size())

    @app.get("/health")
    async def health_check() -> dict:
        """Simple liveness probe for Docker health checks."""
        return {"status": "ok"}

    return app
