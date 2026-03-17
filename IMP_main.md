# Implementation Plan: Core System (Main)

## Overview
This document outlines the core architecture, download pipeline, optimizations, and API layers of the `stock-data-node`. Features and Parquet logic are separated into `IMP_features.md` and `IMP_parquet.md`.

## 1. System Skeleton & Dependencies
The core engine revolves around strict separation of I/O, configuration parsing, API connectivity, and concurrency limitations.
- **ConfigLoader:** Parses `paths.json`, `settings.json`, `gateway.json`, `downloader.json`, and `ticker_map.json`. It provides hot-reloading using file modification times.
- **FailedTickerStore:** Manages `failed_ticker.json` to blacklist symbols, avoiding repeating API fails.
- **TickerResolver:** Maps raw ticker strings to `IBKRContract` objects, bypassing blacklisted items and inferring defaults (`SMART` / `USD`).

## 2. Market Data & Gateway (IB Insync)
- **GatewayClient (`IGatewayClient`):** Manages the `ib_insync` connection.
- **Adaptive Batching:** Detects the market data type via `reqMarketDataType`. Adapts pacing and concurrency based on real `Live` (20 concurrency / 0.1s delay) or `Delayed` (15 concurrency / 0.5s delay) parameters (`BatchConfig`).
- **Connection Watchdog:** Periodically issues `reqCurrentTime` queries proactively if the connection is idle (defaults to 10 mins). If it fails, the system safely triggers a reconnect.
- **Batch Qualification:** Validates incoming contracts concurrently via `qualifyContractsAsync` to verify conId. Error checking classifies specific IBKR problems (e.g. `NO_DATA`, `PACING`, `NO_PERMISSIONS`).

## 3. Rate Limiter & Concurrency 
Designed to strictly respect IBKR's stringent API limits and pace API loads.
- **Dynamic Semaphore Throttling:** Starts optimistically. On an IBKR pacing violation, the throttle drastically halves concurrency and doubles delays (Exponential Backoff). 
- **Recovery Threshold:** Upon achieving 'N' (default 5) consecutive successful requests, concurrency linearly scales back up toward the max. 
- **Request Debounce:** Caches recent `RequestFingerprint` identifiers to prevent identical API calls fired within 15 seconds.

## 4. Workload Pipeline (Downloader & Queues)
- **Priority Queue:** A min-heap queue processing prioritized items (`DownloadPriority`). User API requests hold highest priority, while automated watcher tasks run dynamically underneath. 
- **Downloader Configuration:** `max_chunk_size` dictates maximum API pull duration (e.g., 2 days for 1-minute bars, 30 days for 1-hour bars) to prevent timeouts.
- **Descending Order Chunking:** Pulls historical data chunks beginning from *now*, moving backwards towards the target date, ensuring the most immediately usable data arrives first.
- **Preemption:** Background requests can be suspended mid-chunk if a high-priority user request enters the queue.

## 5. File Watcher & Automated Recovery
Enables a folder-drop mechanism to ingest tasks seamlessly.
- **FileWatcher:** Scans a watch directory for `.txt` files containing comma/newline-separated symbols. Reads them, enqueues the tasks, and dynamically removes the processed content (or the file entirely).
- **Run Logger:** Tracks success/fail logs in `run_log.json` incrementally per batch (without crashing memory). Prints emoji-annotated batch summaries.
- **MarketClock:** Detects NYSE holidays and open times locally to determine exactly if requested timeframes are truly stale or simply experiencing weekend downtime.

## 6. REST API Server & Fast HTTP Interfaces
Runs as a decoupled `FastAPI` instance interacting concurrently with the priority queue.
- `POST /download`: Enqueues an arbitrary user-demanded ticker at maximum priority (`API`). Modifies `timeframes.json` directly.
- `POST /trigger-staleness`: Iterates the local Parquet directory subfolders. Determines what is missing/stale across all known tickers and enqueues massive update tasks at `WATCHER` Priority, yielding a rapid `202 Accepted` response.

## Execution Flow & Testing
Development adheres to an integration-first loop runnable outside Docker. 
- Scripts like `tests/validate_pipeline.py` stand up standalone IB connections, construct mock tasks, verify concurrent pacing logic, and assert the system avoids corruption and correctly sorts dates sequentially.
