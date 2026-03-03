"""
downloader.py — T-005
Core download orchestrator: chunking, delta detection, preemption, phase ordering.
(F-FNC-020, F-FNC-030, F-FNC-060)
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from models import (
    DownloadChunk,
    DownloadPriority,
    DownloadRequest,
    DownloadResult,
    FailedTickerEntry,
    IBKRContract,
    IConfigLoader,
    IFailedTickerStore,
    IGatewayClient,
    IParquetWriter,
    IPriorityQueue,
    IRateLimiter,
    OHLCVBar,
    TickerStatus,
)

logger = logging.getLogger(__name__)

# How many seconds of history to cover per chunk
CHUNK_DURATION_SECONDS = 86400 * 30   # 30 days

# Max history lookback per timeframe (seconds)
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

# Errors that indicate a permanent failure (no point retrying)
PERMANENT_ERROR_PATTERNS = [
    "No security definition",
    "No data of type",
    "No historical data",
    "Invalid contract",
    "Ambiguous contract",
]

# Errors that indicate temporary pacing issues
PACING_ERROR_PATTERNS = [
    "pacing violation",
    "Historical Market Data Service error message:1",
    "Too many requests",
    "Max number of",
]


class Downloader:
    """
    Main download loop.
    Processes DownloadRequests from the priority queue, supports:
    - chunked historical downloads with preemption (API preempts WATCHER mid-chunk)
    - delta detection (only missing bars requested)
    - phase ordering (Daily first, then other timeframes) via queue priority/ordering
    - exponential backoff on pacing errors
    - permanent failure → written to failed_ticker.json blacklist
    """

    def __init__(
        self,
        gateway: IGatewayClient,
        queue: IPriorityQueue,
        writer: IParquetWriter,
        rate_limiter: IRateLimiter,
        config: IConfigLoader,
        failed_store: IFailedTickerStore,
    ) -> None:
        self._gateway = gateway
        self._queue = queue
        self._writer = writer
        self._rate_limiter = rate_limiter
        self._config = config
        self._failed_store = failed_store
        self._running = False

    async def run_loop(self) -> None:
        """Main processing loop. Runs until stop() is called."""
        self._running = True
        logger.info("Downloader started.")

        while self._running:
            self._config.reload_if_changed()

            request = self._queue.dequeue()
            if request is None:
                await asyncio.sleep(1)
                continue

            logger.info(
                "▶ Processing: %s / %s (prio=%s, queue remaining: %d)",
                request.ticker, request.timeframe,
                request.priority.name, self._queue.size()
            )
            try:
                await self._process_request(request)
            except Exception as exc:
                logger.error(
                    "Unexpected error processing %s/%s: %s",
                    request.ticker, request.timeframe, exc, exc_info=True
                )

        logger.info("Downloader stopped.")

    def stop(self) -> None:
        """Signals the download loop to stop after the current request finishes."""
        self._running = False
        logger.info("Downloader stop requested.")

    async def _process_request(self, request: DownloadRequest) -> None:
        """Processes one DownloadRequest with chunking and preemption."""
        request.status = TickerStatus.DOWNLOADING

        if not self._gateway.is_connected():
            logger.error("Gateway disconnected — re-queuing %s/%s", request.ticker, request.timeframe)
            self._queue.enqueue(request)
            await asyncio.sleep(5)
            return

        # Delta detection (F-FNC-030)
        last_ts = self._writer.read_last_timestamp(request.ticker, request.timeframe)
        if last_ts is not None:
            logger.info(
                "Delta download for %s/%s — last bar: %s",
                request.ticker, request.timeframe,
                datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            )
        else:
            logger.info(
                "Full download for %s/%s (no existing data)",
                request.ticker, request.timeframe
            )

        chunks = self._calculate_chunks(last_ts, request.timeframe)
        if not chunks:
            logger.info("No chunks to download for %s/%s — already up to date.", request.ticker, request.timeframe)
            request.status = TickerStatus.DONE
            return

        logger.info(
            "Downloading %s/%s in %d chunk(s)…",
            request.ticker, request.timeframe, len(chunks)
        )

        for i, chunk in enumerate(chunks):
            # Preemption check (F-FNC-020): only WATCHER-priority can be preempted by API
            if (
                request.priority == DownloadPriority.WATCHER
                and self._queue.has_higher_priority_waiting(request.priority)
            ):
                logger.info(
                    "⚡ Preempting %s/%s at chunk %d/%d — higher-priority request waiting",
                    request.ticker, request.timeframe, i + 1, len(chunks)
                )
                self._queue.enqueue(request)
                return

            # Rate limiting
            await self._rate_limiter.acquire()

            # Download the chunk
            result = await self._download_chunk(chunk, request.contract)

            if result.error:
                error_msg = result.error.lower()

                if any(p.lower() in error_msg for p in PACING_ERROR_PATTERNS):
                    logger.warning("Pacing error on %s/%s — applying backoff and retrying chunk", request.ticker, request.timeframe)
                    self._rate_limiter.report_pacing_error()
                    await self._rate_limiter.acquire()
                    # Re-download this chunk
                    result = await self._download_chunk(chunk, request.contract)
                    if result.error:
                        logger.error(
                            "Chunk retry also failed for %s/%s: %s",
                            request.ticker, request.timeframe, result.error
                        )
                        request.status = TickerStatus.FAILED
                        return

                elif any(p.lower() in error_msg for p in PERMANENT_ERROR_PATTERNS):
                    logger.error(
                        "Permanent error for %s/%s: %s — adding to blacklist",
                        request.ticker, request.timeframe, result.error
                    )
                    self._failed_store.add(FailedTickerEntry(
                        ticker=request.ticker,
                        reason=result.error,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="downloader",
                    ))
                    request.status = TickerStatus.FAILED
                    return

                else:
                    logger.error(
                        "Unknown error for %s/%s: %s — skipping chunk",
                        request.ticker, request.timeframe, result.error
                    )
                    continue

            self._rate_limiter.report_success()

            if result.bars:
                self._writer.append_bars(request.ticker, request.timeframe, result.bars)
            else:
                logger.debug("Empty chunk for %s/%s (no new bars)", request.ticker, request.timeframe)

        request.status = TickerStatus.DONE
        logger.info("✅ Done: %s / %s", request.ticker, request.timeframe)

    def _calculate_chunks(
        self, last_ts: Optional[int], timeframe: str
    ) -> list[DownloadChunk]:
        """
        Splits the time range [start, now] into chunks of CHUNK_DURATION_SECONDS.
        If last_ts is provided, downloads only from last_ts+1 (delta).
        """
        now = int(time.time())
        lookback = MAX_HISTORY_LOOKBACK.get(timeframe, 86400 * 365)

        if last_ts is not None:
            start = last_ts + 1
        else:
            start = now - lookback

        if start >= now:
            return []  # Already up to date

        chunks: list[DownloadChunk] = []
        chunk_start = start

        while chunk_start < now:
            chunk_end = min(chunk_start + CHUNK_DURATION_SECONDS, now)
            is_final = (chunk_end >= now)
            chunks.append(DownloadChunk(
                ticker="",  # filled by caller
                timeframe=timeframe,
                start_ts=chunk_start,
                end_ts=chunk_end,
                is_final=is_final,
            ))
            chunk_start = chunk_end + 1

        return chunks

    async def _download_chunk(
        self, chunk: DownloadChunk, contract: Optional[IBKRContract]
    ) -> DownloadResult:
        """Downloads a single time chunk via the gateway."""
        if contract is None:
            return DownloadResult(
                ticker=chunk.ticker,
                timeframe=chunk.timeframe,
                bars=[],
                is_delta=False,
                error="No contract available",
            )
        try:
            bars = await self._gateway.request_historical_bars(
                contract=contract,
                timeframe=chunk.timeframe,
                start_ts=chunk.start_ts,
                end_ts=chunk.end_ts,
            )
            return DownloadResult(
                ticker=chunk.ticker,
                timeframe=chunk.timeframe,
                bars=bars,
                is_delta=True,
            )
        except Exception as exc:
            return DownloadResult(
                ticker=chunk.ticker,
                timeframe=chunk.timeframe,
                bars=[],
                is_delta=False,
                error=str(exc),
            )
