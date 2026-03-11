"""
downloader.py — T-005 + T-IMP-005/009/010
Core download orchestrator: chunking, delta detection, preemption, phase ordering.
(F-FNC-020, F-FNC-030, F-FNC-060, F-IMP-060, F-IMP-100, F-IMP-130)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models import (
    DownloadChunk,
    DownloadPriority,
    DownloadRequest,
    DownloadResult,
    ErrorCategory,
    FailedTickerEntry,
    IBKRContract,
    IConfigLoader,
    IFailedTickerStore,
    IGatewayClient,
    IParquetWriter,
    IPriorityQueue,
    IRateLimiter,
    OHLCVBar,
    RequestFingerprint,
    TickerStatus,
)

logger = logging.getLogger(__name__)

# Logger is already initialized above

# Lookback removed, now in config


def classify_error(error_msg: str) -> ErrorCategory:
    """
    Classifies an IBKR error message into one of 7 categories. (F-IMP-060)
    Supports English and German error messages.
    """
    if not error_msg:
        return ErrorCategory.UNKNOWN

    msg = error_msg.lower()

    if "error 200" in msg or "error 321" in msg or "invalid contract" in msg or "no security definition" in msg:
        return ErrorCategory.INVALID_CONTRACT
    if "qualify" in msg or "ambiguous contract" in msg:
        return ErrorCategory.QUALIFY_FAILED
    if "error 164" in msg or "no data" in msg or "hmds" in msg or "keine daten" in msg or "ergab keine" in msg or "no historical data" in msg:
        return ErrorCategory.NO_DATA
    if "error 162" in msg or "permission" in msg or "not allowed" in msg or "abonnement" in msg:
        return ErrorCategory.NO_PERMISSIONS
    if "timeout" in msg:
        return ErrorCategory.TIMEOUT
    if "error 10090" in msg or "pacing" in msg or "violation" in msg or "too many requests" in msg or "max number of" in msg:
        return ErrorCategory.PACING
    if "cancelled" in msg or "query cancelled" in msg:
        return ErrorCategory.CANCELLED

    return ErrorCategory.UNKNOWN


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
        self._watchlist_dirty = False

    async def run_loop(self) -> None:
        """Main processing loop. Runs until stopped."""
        self._running = True
        logger.info("✅ Downloader started.")

        while self._running:
            self._config.reload_if_changed()

            request = self._queue.dequeue()
            if request is None:
                if self._watchlist_dirty:
                    self._update_master_watchlist()
                    self._watchlist_dirty = False
                await asyncio.sleep(1)
                continue

            # Connection Watchdog (F-OPT-050): check health before each request
            try:
                await self._gateway.ensure_connected()
            except ConnectionError as exc:
                logger.error("❌ Watchdog reconnect failed: %s — re-queuing %s", exc, request.ticker)
                self._queue.enqueue(request)
                await asyncio.sleep(5)
                continue

            logger.info(
                "▶ Processing: %s / %s (prio=%s, queue remaining: %d)",
                request.ticker, request.timeframe,
                request.priority.name, self._queue.size()
            )
            try:
                await self._process_request(request)
                self._watchlist_dirty = True
            except Exception as exc:
                logger.error(
                    "❌ Unexpected error processing %s/%s: %s",
                    request.ticker, request.timeframe, exc, exc_info=True
                )

        logger.info("🛑 Downloader stopped.")

    def stop(self) -> None:
        """Signals the download loop to stop after the current request finishes."""
        self._running = False
        logger.info("Downloader stop requested.")

    async def _process_request(self, request: DownloadRequest) -> None:
        """Processes one DownloadRequest with chunking, preemption, and batch progress."""
        request.status = TickerStatus.DOWNLOADING
        request_start = time.monotonic() # Metrics
        chunk_ok: int = 0
        chunk_fail: int = 0
        total_bars: int = 0

        if not self._gateway.is_connected():
            logger.error("❌ Gateway disconnected — re-queuing %s/%s", request.ticker, request.timeframe)
            self._queue.enqueue(request)
            await asyncio.sleep(5)
            return
            
        if request.contract is None:
            logger.info("ℹ️  No contract provided for %s. Attempting auto-discovery...", request.ticker)
            new_contract = await self._auto_discover_contract(request.ticker)
            if new_contract:
                request.contract = new_contract
            else:
                logger.error("❌ Auto-discovery failed for %s. Blacklisting.", request.ticker)
                self._failed_store.add(FailedTickerEntry(
                    ticker=request.ticker,
                    reason="Unmapped & Auto-discovery failed",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source="downloader",
                ))
                if hasattr(self._config, "register_unmapped_ticker"):
                    self._config.register_unmapped_ticker(request.ticker)
                request.status = TickerStatus.FAILED
                return

        # Delta detection (F-FNC-030)
        last_ts = self._writer.read_last_timestamp(request.ticker, request.timeframe)
        if last_ts:
            logger.info(
                "ℹ️  Delta download for %s/%s (appending from %s)",
                request.ticker, request.timeframe,
                datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M:%S")
            )
        else:
            logger.info("ℹ️  Full download for %s/%s (no existing data)", request.ticker, request.timeframe)

        chunks: list[DownloadChunk] = self._calculate_chunks(
            last_ts=last_ts, timeframe=request.timeframe, exchange=request.contract.exchange
        )
        if not chunks:
            logger.info("✅ No chunks to download for %s/%s — already up to date.", request.ticker, request.timeframe)
            request.status = TickerStatus.DONE
            return

        logger.info(
            "▶️  Downloading %s/%s in %d chunk(s)...",
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
                return  # preempted — no batch summary

            # Rate limiting
            await self._rate_limiter.acquire()

            # Download the chunk
            result = await self._download_chunk(chunk, request.contract)

            if result.error:
                category = classify_error(result.error)

                if category == ErrorCategory.PACING:
                    logger.warning("⏳ Pacing error on %s/%s — applying backoff and retrying chunk", request.ticker, request.timeframe)
                    self._rate_limiter.report_pacing_error()
                    await self._rate_limiter.acquire()
                    result = await self._download_chunk(chunk, request.contract)
                    if result.error:
                        logger.error(
                            "❌ Chunk retry also failed for %s/%s: %s",
                            request.ticker, request.timeframe, result.error
                        )
                        chunk_fail += 1
                        request.status = TickerStatus.FAILED
                        break

                elif category == ErrorCategory.QUALIFY_FAILED:
                    logger.warning("⚠️ Qualify failed for %s — attempting auto-discovery fallback.", request.ticker)
                    new_contract = await self._auto_discover_contract(request.ticker)
                    if new_contract:
                        request.contract = new_contract
                        await self._rate_limiter.acquire()
                        result = await self._download_chunk(chunk, request.contract)
                        
                    if not new_contract or result.error:
                        logger.error(
                            "❌ Permanent error for %s/%s: %s — adding to blacklist and mapping to SKIP",
                            request.ticker, request.timeframe, result.error
                        )
                        self._failed_store.add(FailedTickerEntry(
                            ticker=request.ticker,
                            reason=result.error or "Auto-discovery failed",
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            source="downloader",
                        ))
                        # Also register as null mapping so we don't spam IBKR on restart
                        if hasattr(self._config, "register_unmapped_ticker"):
                            self._config.register_unmapped_ticker(request.ticker)
                        
                        chunk_fail = chunk_fail + 1
                        request.status = TickerStatus.FAILED
                        break

                elif category == ErrorCategory.NO_DATA:
                    # If this is a full download (fetching backwards in time), hitting NO_DATA 
                    # usually means we've reached the IPO date or the end of the exchange's history.
                    # We can safely abort fetching even older chunks.
                    if not last_ts:
                        logger.info(
                            "ℹ️ Reached beginning of history (IPO) for %s/%s at chunk %d/%d — stopping early.",
                            request.ticker, request.timeframe, i + 1, len(chunks)
                        )
                        break
                    else:
                        # For delta downloads (fetching forwards), maybe a weekend/holiday chunk is empty.
                        logger.debug(
                            "ℹ️ No data for %s/%s chunk %d/%d — skipping (not a permanent error)",
                            request.ticker, request.timeframe, i + 1, len(chunks)
                        )
                        chunk_fail = chunk_fail + 1
                        continue

                elif category == ErrorCategory.NO_PERMISSIONS:
                    logger.error(
                        "❌ Permission error for %s/%s: %s — stopping",
                        request.ticker, request.timeframe, result.error
                    )
                    chunk_fail = chunk_fail + 1
                    request.status = TickerStatus.FAILED
                    break

                elif category == ErrorCategory.INVALID_CONTRACT:
                    logger.error(
                        "❌ Invalid contract error for %s/%s: %s — adding to blacklist and stopping",
                        request.ticker, request.timeframe, result.error
                    )
                    self._failed_store.add(FailedTickerEntry(
                        ticker=request.ticker,
                        reason=result.error or "Invalid Contract",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="downloader",
                    ))
                    if hasattr(self._config, "register_unmapped_ticker"):
                        self._config.register_unmapped_ticker(request.ticker)
                    chunk_fail = chunk_fail + 1
                    request.status = TickerStatus.FAILED
                    break

                elif category == ErrorCategory.TIMEOUT:
                    # Target behavior: Skip entire ticker on TIMEOUT instead of just chunk
                    logger.error(
                        "❌ TIMEOUT error for %s/%s: %s — skipping remaining chunks for this ticker",
                        request.ticker, request.timeframe, result.error
                    )
                    chunk_fail = chunk_fail + 1
                    request.status = TickerStatus.FAILED
                    break

                else:
                    logger.error(
                        "❌ %s error for %s/%s: %s — skipping chunk",
                        category.value, request.ticker, request.timeframe, result.error
                    )
                    chunk_fail = chunk_fail + 1
                    continue

            self._rate_limiter.report_success()

            if result.bars:
                self._writer.append_bars(request.ticker, request.timeframe, result.bars)
                chunk_ok = chunk_ok + 1
                total_bars = total_bars + len(result.bars)
            else:
                logger.debug("Empty chunk for %s/%s (no new bars)", request.ticker, request.timeframe)
                chunk_ok = chunk_ok + 1  # empty but successful

        # Batch progress reporting (F-IMP-130)
        elapsed = time.monotonic() - request_start
        total_chunks = chunk_ok + chunk_fail
        
        # Self-healing blacklist (T-EXT-003): Remove from blacklist if successful
        if total_bars > 0 and self._failed_store.is_blacklisted(request.ticker):
            logger.info("✅ Auto-cleaning %s from blacklist due to successful download.", request.ticker)
            self._failed_store.remove(request.ticker)

        if request.status != TickerStatus.FAILED:
            request.status = TickerStatus.DONE
        logger.info(
            "📊 %s: %s/%s — %d/%d chunks OK, %s bars, %.1fs",
            "✅" if request.status == TickerStatus.DONE else "❌",
            request.ticker, request.timeframe,
            chunk_ok, total_chunks, 
            f"{total_bars:,d}".replace(",", "."), 
            elapsed,
        )
        # Watchlist generation is now deferred until the queue hits 0 (handled in run_loop)

    def _update_master_watchlist(self) -> None:
        """
        Updates the all.json master watchlist with all tickers found in the parquet directory.
        F-DAT-030, F-DAT-040
        """
        try:
            paths = self._config.get_paths_config()
            parquet_dir = Path(paths.parquet_dir)
            watchlist_dir = Path(paths.watchlist_dir)
            
            if not parquet_dir.exists():
                return
                
            tickers = sorted([f.name for f in parquet_dir.iterdir() if f.is_dir()])
            
            watchlist_dir.mkdir(parents=True, exist_ok=True)
            file_path = watchlist_dir / "all.txt"
            tmp_path = file_path.with_suffix(".tmp")
            
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write("\n".join(tickers))
            os.replace(tmp_path, file_path)
            logger.debug("✅ Updated master watchlist all.txt with %d tickers.", len(tickers))
        except Exception as e:
            logger.error("❌ Failed to update all.json: %s", e)
            # cleanup tmp file if it exists
            # (tmp_path is in try block, need to make sure it's defined or handle)
            pass

    def _calculate_chunks(
        self, last_ts: Optional[int], timeframe: str, exchange: str
    ) -> list[DownloadChunk]:
        """
        Splits the time range [start, now] into chunks.
        If last_ts is provided, downloads only from last_ts+1 (delta).
        Chunks are returned in descending order (newest first) for faster
        access to current data. (F-IMP-100)
        """
        now = int(time.time())
        dl_cfg = self._config.get_downloader_config()

        # F-IMP-110: Bound effective current time to the end of the last completed trading day for 1D
        if timeframe == "1D":
            from datetime import datetime as dt, time as dt_time
            from market_clock import MarketClock
            
            latest_day = MarketClock.get_latest_completed_trading_day(exchange=exchange)
            tz, _, _, _ = MarketClock._get_exchange_config(exchange)
            
            
            end_of_day_dt = tz.localize(dt.combine(latest_day, dt_time(23, 59, 59)))
            effective_now = int(end_of_day_dt.timestamp())
        else:
            effective_now = now
        
        # Determine lookback
        lookback = dl_cfg.max_history_lookback.get(timeframe, 86400 * 365)

        if last_ts is not None:
            start = last_ts + 1
            # If our last timestamp is already starting on or after our effective_now
            # (e.g. 00:00:00 of today, while effective_now is 23:59:59 of yesterday)
            # then we shouldn't download anything.
            if timeframe == "1D":
                from datetime import datetime as dt, timezone as tz
                last_dt = dt.fromtimestamp(last_ts, tz=tz.utc)
                if last_dt.date() >= latest_day:
                    return []
        else:
            start = effective_now - lookback

        if start >= effective_now:
            return []  # Already up to date

        # Determine chunk size, capped by max_chunk_size (F-OPT-040)
        chunk_size = dl_cfg.chunk_duration.get(timeframe, dl_cfg.chunk_duration.get("default", 2592000))
        if dl_cfg.max_chunk_size:
            max_size = dl_cfg.max_chunk_size.get(timeframe, dl_cfg.max_chunk_size.get("default", chunk_size))
            chunk_size = min(chunk_size, max_size)
        chunks: list[DownloadChunk] = []
        chunk_start = start

        while chunk_start < effective_now:
            chunk_end = min(chunk_start + chunk_size, effective_now)
            is_final = (chunk_end >= effective_now)
            chunks.append(DownloadChunk(
                ticker="",  # filled by caller
                timeframe=timeframe,
                start_ts=chunk_start,
                end_ts=chunk_end,
                is_final=is_final,
            ))
            chunk_start = chunk_end + 1

        # Descending order: newest chunks first (F-IMP-100)
        chunks.reverse()

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

        # Request debounce (F-OPT-030): skip if identical request sent too recently
        fingerprint = RequestFingerprint(
            symbol=contract.symbol,
            timeframe=chunk.timeframe,
            start_ts=chunk.start_ts,
            end_ts=chunk.end_ts,
        )
        if self._rate_limiter.check_debounce(fingerprint):
            logger.debug(
                "⏭️ Debounce: skipping duplicate request for %s/%s",
                contract.symbol, chunk.timeframe,
            )
            return DownloadResult(
                ticker=chunk.ticker,
                timeframe=chunk.timeframe,
                bars=[],
                is_delta=False,
            )

        try:
            bars = await self._gateway.request_historical_bars(
                contract=contract,
                timeframe=chunk.timeframe,
                start_ts=chunk.start_ts,
                end_ts=chunk.end_ts,
            )
            self._rate_limiter.record_request(fingerprint)
            return DownloadResult(
                ticker=chunk.ticker,
                timeframe=chunk.timeframe,
                bars=bars,
                is_delta=True,
            )
        except Exception as exc:
            self._rate_limiter.record_request(fingerprint)
            return DownloadResult(
                ticker=chunk.ticker,
                timeframe=chunk.timeframe,
                bars=[],
                is_delta=False,
                error=str(exc),
            )

    async def _auto_discover_contract(self, ticker: str) -> Optional[IBKRContract]:
        """
        Attempts to find a matching STK contract using reqMatchingSymbols.
        Returns the best matched IBKRContract based on priority, or None if no match.
        """
        results = await self._gateway.search_contract(ticker)
        
        # Filter strictly for STK
        stk_results = [r for r in results if r.contract.secType == "STK"]
        if not stk_results:
            logger.warning("Auto-discovery found no STK contracts for %s", ticker)
            return None
            
        auto_cfg = self._config.get_auto_discovery_config()
        
        # Scoring function: lower score = better priority
        def score(desc) -> tuple[int, int]:
            c = desc.contract
            # Currency priority
            curr = c.currency
            curr_score = 999
            if curr in auto_cfg.currency_priority:
                curr_score = auto_cfg.currency_priority.index(curr)
                
            # Exchange priority
            exch = c.primaryExchange or c.exchange
            exch_score = 999
            if exch in auto_cfg.exchange_priority:
                exch_score = auto_cfg.exchange_priority.index(exch)
                
            return (curr_score, exch_score)
            
        # Sort by best scores
        stk_results.sort(key=score)
        best = stk_results[0]
        
        new_contract = IBKRContract(
            symbol=best.contract.symbol,
            exchange=best.contract.primaryExchange or best.contract.exchange,
            currency=best.contract.currency,
            sec_type="STK"
        )
        
        # Persist the new mapping (F-EXT-070)
        if hasattr(self._config, "update_ticker_map"):
            self._config.update_ticker_map(ticker, new_contract)
            
        logger.info(
            "✅ Auto-discovered %s -> Exchange: %s, Currency: %s",
            ticker, new_contract.exchange, new_contract.currency
        )
        return new_contract
