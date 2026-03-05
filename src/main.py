"""
main.py — T-012
Application entrypoint. Wires all components via dependency injection,
runs startup checks, connects to gateway, starts background tasks.
(F-LOG-010, F-LOG-020, F-SYS-010)
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import os
from pathlib import Path

import uvicorn

# ─── Bootstrap: ensure src/ is on the path when running from project root ──
sys.path.insert(0, str(Path(__file__).parent))

from api_server import create_api
from config_loader import ConfigLoader
from downloader import Downloader
from failed_ticker_store import FailedTickerStore
from file_watcher import FileWatcher
from gateway_client import GatewayClient
from parquet_writer import ParquetWriter
from priority_queue import DownloadQueue
from rate_limiter import AdaptiveRateLimiter
from startup_checks import StartupChecker
from ticker_resolver import TickerResolver

# ─── Logging Setup ───────────────────────────────────────────────

def configure_logging(log_dir: str) -> None:
    """
    Two handlers:
      - StreamHandler (stdout): DEBUG level, verbose structured format (F-LOG-020)
      - FileHandler (error.log): ERROR level only, crash-safe (F-LOG-010)
    """
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Terminal — verbose
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # File — errors only
    file_handler = logging.FileHandler(log_dir_path / "error.log", encoding="utf-8")
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Reduce noise from libraries
    logging.getLogger("ib_insync").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ─── Main ────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

FILE_WATCHER_INTERVAL = 5.0   # seconds between watch directory scans


async def main() -> None:
    # Determine config root (can be overridden by env var for Docker)
    base_dir = Path(os.environ.get("APP_BASE_DIR", Path(__file__).parent.parent))
    config_dir = str(base_dir / "config")
    log_dir = str(base_dir / "logs")

    configure_logging(log_dir)

    logger.info("═══════════════════════════════════════════════")
    logger.info("  Stock Data Node — starting up")
    logger.info("═══════════════════════════════════════════════")

    # ── Load configuration ─────────────────────────────────────
    try:
        config = ConfigLoader(config_dir=config_dir, parquet_dir="")  # parquet_dir filled below
        paths = config.get_paths_config()
        # Recreate with correct parquet_dir now that paths are loaded
        config = ConfigLoader(config_dir=config_dir, parquet_dir=paths.parquet_dir)
    except FileNotFoundError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    # Ensure required directories exist
    Path(paths.parquet_dir).mkdir(parents=True, exist_ok=True)
    Path(paths.watch_dir).mkdir(parents=True, exist_ok=True)
    state_dir = base_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # ── Instantiate components (dependency injection) ──────────
    writer       = ParquetWriter(paths.parquet_dir)
    failed_store = FailedTickerStore(str(state_dir / "failed_ticker.json"))
    resolver     = TickerResolver(config, failed_store)
    queue        = DownloadQueue()
    rate_limiter = AdaptiveRateLimiter()
    gateway      = GatewayClient(config.get_gateway_config())
    downloader   = Downloader(gateway, queue, writer, rate_limiter, config, failed_store)
    watcher      = FileWatcher(paths.watch_dir, queue, resolver, config, failed_store)
    api          = create_api(queue, resolver, config, failed_store, watcher)

    # ── Startup checks (F-SYS-020) ────────────────────────────
    checker = StartupChecker(paths, writer)
    checker.run_all_checks()

    # ── Connect to gateway (F-CON-010, F-SYS-030) ─────────────
    try:
        await gateway.connect()
    except ConnectionError as exc:
        logger.critical("Cannot connect to IB Gateway: %s", exc)
        sys.exit(1)

    # ── Detect market data type (F-CON-020, F-IMP-010/020) ────────
    batch_config = await gateway.detect_market_data_type()
    rate_limiter.configure(batch_config)
    logger.info(
        "Market data config: %s (concurrent=%d, pacing=%.1fs)",
        batch_config.description, batch_config.max_concurrent, batch_config.base_pacing_delay,
    )

    # ── Signal handling ────────────────────────────────────────
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: signal.Signals) -> None:
        logger.info("Received signal %s — initiating shutdown…", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _signal_handler(s))

    # ── Background tasks ───────────────────────────────────────

    async def file_watcher_loop() -> None:
        logger.info("File watcher started (interval: %.0fs)", FILE_WATCHER_INTERVAL)
        while not shutdown_event.is_set():
            try:
                watcher.scan_once()
            except Exception as exc:
                logger.error("File watcher error: %s", exc, exc_info=True)
            await asyncio.sleep(FILE_WATCHER_INTERVAL)

    async def api_server_loop() -> None:
        cfg = uvicorn.Config(
            app=api,
            host="0.0.0.0",
            port=8000,
            log_level="warning",
        )
        server = uvicorn.Server(cfg)
        logger.info("REST API listening on http://0.0.0.0:8000")
        await server.serve()

    async def shutdown_watcher() -> None:
        await shutdown_event.wait()
        logger.info("Shutdown signal received — stopping downloader…")
        downloader.stop()

    # ── Run everything concurrently ────────────────────────────
    logger.info("All systems go — entering main loop.")
    await asyncio.gather(
        file_watcher_loop(),
        api_server_loop(),
        downloader.run_loop(),
        shutdown_watcher(),
        return_exceptions=True,
    )

    # ── Graceful shutdown ──────────────────────────────────────
    logger.info("Disconnecting from IB Gateway…")
    await gateway.disconnect()
    logger.info("Stock Data Node stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
