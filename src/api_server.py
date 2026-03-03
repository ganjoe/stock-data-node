"""
api_server.py — T-008
Minimal FastAPI REST API for ticker download requests. (F-INT-010, F-CFG-050)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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

        # Resolve ticker to IBKR contract
        contract = resolver.resolve(ticker)
        if contract is None:
            reason = (
                "Ticker is blacklisted."
                if failed_store.is_blacklisted(ticker)
                else f"Ticker '{ticker}' not found in ticker_map.json."
            )
            logger.warning("API: cannot resolve %s — %s", ticker, reason)
            if not failed_store.is_blacklisted(ticker):
                # Unknown tickers go to failed store
                failed_store.add(FailedTickerEntry(
                    ticker=ticker,
                    reason="Not found in ticker_map.json",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source="api",
                ))
            raise HTTPException(status_code=404, detail=reason)

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

    @app.get("/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Returns current queue depth."""
        return StatusResponse(queue_size=queue.size())

    @app.get("/health")
    async def health_check() -> dict:
        """Simple liveness probe for Docker health checks."""
        return {"status": "ok"}

    return app
