"""
api_server.py — T-008
Minimal FastAPI REST API for ticker download requests. (F-INT-010, F-CFG-050)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from file_watcher import FileWatcher

from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.features.job_manager import JobManager
from src.features.config_parser import FeatureConfigParser, ProcessingContext, FeatureType
from src.features.calculator import TechnicalCalculator
from src.features.parquet_io import ParquetStorage
from src.features.processor import FeatureProcessor

from models import (
    DownloadPriority,
    DownloadRequest,
    FailedTickerEntry,
    IConfigLoader,
    IFailedTickerStore,
    IPriorityQueue,
    ITickerResolver,
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


class StatusResponse(BaseModel):
    queue_size: int


# ─── Factory ─────────────────────────────────────────────────────

def create_api(
    queue: IPriorityQueue,
    resolver: ITickerResolver,
    config: IConfigLoader,
    failed_store: IFailedTickerStore,
    watcher: "FileWatcher",
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

        # Check if explicitly blacklisted or skipped
        if resolver.is_ignored(ticker):
            logger.warning("❌ Rejected API download for %s (blacklisted/SKIP)", ticker)
            raise HTTPException(status_code=400, detail=f"Ticker {ticker} is blacklisted or mapped to SKIP")
            
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
        
        logger.info("▶️  API: Triggering staleness check for %d ticker(s)", len(all_tickers))
        count = 0
        for ticker in all_tickers:
            # Only enqueue if the ticker is not explicitly ignored
            if resolver.is_ignored(ticker):
                continue
            
            # 4. Fetch timeframes and enqueue
            timeframes = config.get_timeframes_for_ticker(ticker)
            for tf in timeframes:
                contract = resolver.resolve(ticker)
                req = DownloadRequest(
                    ticker=ticker,
                    timeframe=tf,
                    priority=DownloadPriority.WATCHER,
                    contract=contract,
                )
                queue.enqueue(req)
                count += 1
        
        logger.info("✅ API: Enqueued %d staleness requests", count)

        # 5. Return JSONResponse with 202
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": "accepted", "tickers_evaluated": count}
        )

    @app.post("/features/calculate")
    async def trigger_feature_calculation(background_tasks: BackgroundTasks) -> JSONResponse:
        """
        Manually triggers the feature calculation process.
        Returns 202 if started, 409 if already running. (F-API-010, F-SYS-030)
        """
        job_manager = JobManager()
        
        def run_feature_pipeline():
            # Setup context from config
            paths = config.get_paths_config()
            config_parser = FeatureConfigParser(str(Path(config.config_dir) / "features.json"))
            features = config_parser.parse()
            
            # Note: thread_count could be moved to a config file eventually
            ctx = ProcessingContext(
                thread_count=paths.processing_threads, 
                data_dir=paths.parquet_dir,
                timeframes=["1D"], # Default focus
                features=features
            )
            
            storage = ParquetStorage(ctx.data_dir)
            calculator = TechnicalCalculator()
            processor = FeatureProcessor(ctx, storage, calculator)
            
            tickers = storage.get_available_tickers()
            logger.info("Starting feature calculation for %d tickers", len(tickers))
            results = processor.process_all_tickers(tickers)
            success_count = sum(1 for r in results if r.success)
            logger.info("Feature calculation finished: %d/%d successful", success_count, len(results))

        success = job_manager.start_feature_calculation(run_feature_pipeline)
        
        if success:
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={"status": "Job started in background"}
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"status": "Ignored", "detail": "A feature calculation process is already running."}
            )

    @app.get("/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Returns current queue depth."""
        return StatusResponse(queue_size=queue.size())

    @app.get("/health")
    async def health_check() -> dict:
        """Simple liveness probe for Docker health checks."""
        return {"status": "ok"}

    return app
