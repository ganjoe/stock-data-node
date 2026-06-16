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
import re
import urllib.request
import urllib.error
from pathlib import Path

import uvicorn

# ─── Bootstrap: ensure src/ is on the path when running from project root ──
sys.path.insert(0, str(Path(__file__).parent))

from api_server import create_api
from config_loader import ConfigLoader
from downloader import Downloader
from fallback_downloader import FallbackDownloader
from failed_ticker_store import FailedTickerStore
from file_watcher import FileWatcher
from gateway_client import GatewayClient
from parquet_writer import ParquetWriter
from priority_queue import DownloadQueue
from rate_limiter import AdaptiveRateLimiter
from startup_checks import StartupChecker
from ticker_resolver import TickerResolver
from mqtt_listener import MQTTListener

# ─── Logging Setup ───────────────────────────────────────────────

# ANSI codes for terminal colors
GREY = "\033[90m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD_RED = "\033[31;1m"
RESET = "\033[0m"

# Regex for highlighting
TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
NUMBER_RE = re.compile(r"(\b\d+(\.\d+)?\b)")

class ColoredFormatter(logging.Formatter):
    """
    Custom formatter providing:
    - Fixed column widths (F-LOG-050)
    - ANSI colors based on log level (F-LOG-030)
    - In-text highlighting for tickers and numbers
    """
    COLORS = {
        logging.DEBUG: GREY,
        logging.INFO: RESET,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        # 1. Color the level name and message
        color = self.COLORS.get(record.levelno, RESET)
        
        # 2. In-text highlighting for ticker symbols (Cyan) and numbers (Magenta)
        # We only highlight the message part
        msg = str(record.msg)
        if record.args:
            try:
                msg = msg % record.args
            except Exception:
                pass
        
        # Highlight tickers (all caps, 1-5 chars)
        msg = TICKER_RE.sub(f"{CYAN}\\1{RESET}{color}", msg)
        # Highlight numbers
        msg = NUMBER_RE.sub(f"{MAGENTA}\\1{RESET}{color}", msg)
        
        # 3. Format the final output with fixed widths
        # Columns: Time (8) | Level (8) | Module (20) | Message
        time_str = self.formatTime(record, "%H:%M:%S")
        level_str = record.levelname.ljust(8)
        module_str = record.name[:20].ljust(20)
        
        # Special case for separators (F-LOG-060)
        if "════" in msg:
            return f"{color}{msg}{RESET}"
            
        return f"{time_str} | {color}{level_str}{RESET} | {GREY}{module_str}{RESET} | {color}{msg}{RESET}"

def configure_logging(log_dir: str) -> None:
    """
    Two handlers:
      - StreamHandler (stdout): DEBUG level, verbose structured format (F-LOG-020)
      - FileHandler (error.log): ERROR level only, crash-safe (F-LOG-010)
    """
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
    datefmt = "%d.%m.%Y %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Terminal — verbose
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(ColoredFormatter())
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


# ─── Feature Service Integration ─────────────────────────────────

FEATURE_SERVICE_URL = os.environ.get(
    "FEATURE_SERVICE_URL", "http://localhost:8003/features/calculate"
)
FEATURE_SERVICE_TIMEOUT = int(os.environ.get("FEATURE_SERVICE_TIMEOUT", "600"))


def _trigger_feature_service(label: str) -> bool:
    """
    Triggers feature calculation via HTTP POST to the stock-data-features service.
    Returns True on success, False on error (non-blocking to the download pipeline).
    """
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("  %s — Triggering Feature Service", label)
    logger.info("═══════════════════════════════════════════════════════════════")
    try:
        req = urllib.request.Request(FEATURE_SERVICE_URL, method="POST")
        with urllib.request.urlopen(req, timeout=FEATURE_SERVICE_TIMEOUT) as resp:
            status_code = resp.getcode()
            body = resp.read().decode("utf-8")
            if status_code in (200, 202):
                logger.info("✅ Feature service accepted: %s", body)
                return True
            else:
                logger.warning("⚠️  Feature service responded %d: %s", status_code, body)
                return False
    except urllib.error.URLError as exc:
        logger.error("❌ Could not reach feature service at %s: %s — continuing anyway", FEATURE_SERVICE_URL, exc)
        return False
    except Exception as exc:
        logger.error("❌ Feature service error: %s — continuing anyway", exc)
        return False


async def main() -> None:
    # Determine config root (can be overridden by env var for Docker)
    base_dir = Path(os.environ.get("APP_BASE_DIR", Path(__file__).parent.parent))
    config_dir = str(base_dir / "config")
    log_dir = str(base_dir / "logs")

    configure_logging(log_dir)

    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("  Stock Data Node — starting up")
    logger.info("═══════════════════════════════════════════════════════════════")

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
    gateway      = GatewayClient(config)
    downloader   = Downloader(gateway, queue, writer, rate_limiter, config, failed_store)
    fallback     = FallbackDownloader(writer, config)
    watcher      = FileWatcher(paths.watch_dir, queue, resolver, config, failed_store)
    api          = create_api(queue, resolver, config, failed_store, watcher, writer, gateway)
    
    # ── MQTT Listener ──────────────────────────────────────────
    mqtt_host = os.environ.get("MQTT_HOST", "localhost")
    mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
    mqtt_listener = MQTTListener(mqtt_host, mqtt_port, queue, resolver, failed_store, config)
    mqtt_listener.start()

    # ── Startup checks (F-SYS-020) ────────────────────────────
    checker = StartupChecker(paths, writer)
    checker.run_all_checks()

    # ── Initial Feature Calculation (F-LC-020) ───────────────
    # Trigger feature service *before* API or Downloader starts.
    _trigger_feature_service("Initial Feature Calculation (F-LC-020)")

    # ── Initial Staleness Sweep ──────────────────────────────
    from api_server import enqueue_staleness_sweep
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("  Starting Initial Staleness Sweep")
    logger.info("═══════════════════════════════════════════════════════════════")
    enqueue_staleness_sweep(watcher, config, resolver, queue, writer)

    # ── Cycle-Complete Callback (F-LC-011) ─────────
    # Trigger feature service if new data was downloaded.
    async def _on_download_cycle_complete() -> None:
        await asyncio.to_thread(_trigger_feature_service, "Post-Cycle Feature Calculation (F-LC-011)")

    downloader.set_on_cycle_complete(_on_download_cycle_complete)

    # ── Connect to gateway (F-CON-010, F-SYS-030) ─────────────
    connected = False
    try:
        await gateway.connect()
        connected = True
        logger.info("✅ Connected to IB Gateway.")
    except Exception as exc:
        logger.warning("⚠️  Cannot connect to IB Gateway: %s. Continuing in API-only mode.", exc)

    # ── Detect market data type (F-CON-020, F-IMP-010/020) ────────
    settings = config.get_settings_config()
    if connected:
        try:
            batch_config = await gateway.detect_market_data_type()
            rate_limiter.configure(batch_config, settings)
            logger.info(
                "ℹ️  Market data config: %s (concurrent=%d, pacing=%.1fs)",
                batch_config.description, batch_config.max_concurrent, batch_config.base_pacing_delay,
            )
        except Exception as exc:
            logger.error("❌ Failed to detect market data type: %s", exc)
    else:
        logger.info("ℹ️  Skipping market data type detection (no gateway connection).")

    # ── Performance-Optimized Logging (F-OPT-070) ──────────────
    bulk_level = getattr(logging, settings.bulk_log_level.upper(), logging.INFO)
    logging.getLogger("ib_insync").setLevel(bulk_level)
    logger.info("ℹ️  Bulk logging level set to %s", settings.bulk_log_level)

    # ── Signal handling ────────────────────────────────────────
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: signal.Signals) -> None:
        logger.info("🛑 Received signal %s — initiating shutdown...", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _signal_handler(s))

    # ── Background tasks ───────────────────────────────────────

    async def file_watcher_loop() -> None:
        interval = config.get_settings_config().file_watcher_interval
        logger.info("ℹ️  File watcher started (interval: %.0fs)", interval)
        while not shutdown_event.is_set():
            try:
                watcher.scan_once()
            except Exception as exc:
                logger.error("File watcher error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def api_server_loop() -> None:
        api_cfg = config.get_gateway_config().api
        cfg = uvicorn.Config(
            app=api,
            host=api_cfg.host,
            port=api_cfg.port,
            log_level="warning",
        )
        server = uvicorn.Server(cfg)
        logger.info("ℹ️  REST API listening on http://%s:%d", api_cfg.host, api_cfg.port)
        try:
            await server.serve()
        except OSError as e:
            if e.errno == 98:
                logger.critical("❌ Port 8002 is already in use. Please check if another instance (e.g. Docker) is running.")
            else:
                logger.error("❌ API server error: %s", e)
            shutdown_event.set()

    async def staleness_sweep_loop() -> None:
        interval = 3600.0  # Run every 60 minutes
        logger.info("ℹ️  Staleness sweep background task started (interval: %.0fs)", interval)
        while not shutdown_event.is_set():
            # Wait first, since we do an initial sweep on startup
            try:
                # Use wait_for so we can break early if shutdown_event is set
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
                break  # shutdown_event was set, exit loop
            except asyncio.TimeoutError:
                pass  # timeout means interval elapsed, run the sweep

            if shutdown_event.is_set():
                break

            logger.info("═══════════════════════════════════════════════════════════════")
            logger.info("  Periodic Download Cycle — Staleness Sweep (F-LC-012)")
            logger.info("═══════════════════════════════════════════════════════════════")
            try:
                watcher.scan_once()
                enqueue_staleness_sweep(watcher, config, resolver, queue, writer)
            except Exception as exc:
                logger.error("❌ Periodic staleness sweep error: %s", exc, exc_info=True)

    async def shutdown_watcher() -> None:
        await shutdown_event.wait()
        logger.info("Shutdown signal received — stopping downloader…")
        downloader.stop()
        fallback.stop()
        mqtt_listener.stop()

    # ── Run everything concurrently ────────────────────────────
    logger.info("✅ All systems go — entering main loop.")
    await asyncio.gather(
        file_watcher_loop(),
        api_server_loop(),
        staleness_sweep_loop(),
        downloader.run_loop(),
        fallback.run_loop(),
        shutdown_watcher(),
        return_exceptions=True,
    )

    # ── Graceful shutdown ──────────────────────────────────────
    logger.info("Disconnecting from IB Gateway…")
    await gateway.disconnect()
    logger.info("Stock Data Node stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
