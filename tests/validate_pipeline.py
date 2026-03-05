"""
validate_pipeline.py — T-014
End-to-end validation script for the stock-data-node pipeline.
Run directly from IDE — no Docker required.

Prerequisites:
  - IB Gateway or TWS running and authenticated (Paper or Live)
  - pip install ib_insync pyarrow fastapi uvicorn

Usage:
  python tests/validate_pipeline.py

What it does:
  1. Creates temporary config files and directories (_validation_tmp/)
  2. Connects to IB Gateway on localhost
  3. Detects market data type (Live/Delayed)
  4. Creates a watch file with test tickers
  5. Processes the watch file through the full pipeline
  6. Runs the downloader until the queue is empty
  7. Scans resulting parquet files and prints sample data
  8. Prints a validation summary
  9. Optionally cleans up the temp directory
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

# Add src to Python path
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import uvicorn
import urllib.request

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

# ─── Validation settings ─────────────────────────────────────────

TEST_TICKERS = ["AAPL", "MSFT", "GOOG"]

GATEWAY_HOST = "0.0.0.0"#bei ib-gateway-app docker gateway ip eintragen z.b. 172.17.0.1
GATEWAY_PORT = 4002          # Paper trading port (change to 4001 for live)
GATEWAY_MODE = "paper"

BASE_DIR = Path(__file__).parent.parent
VALIDATION_DIR = BASE_DIR / "_validation_tmp"


class PipelineValidator:
    """Orchestrates the full end-to-end validation of the download pipeline."""

    def __init__(self) -> None:
        self._config_dir  = VALIDATION_DIR / "config"
        self._data_dir    = VALIDATION_DIR / "data" / "parquet"
        self._watch_dir   = VALIDATION_DIR / "watch"
        self._logs_dir    = VALIDATION_DIR / "logs"
        self._state_dir   = VALIDATION_DIR / "state"

        self._gateway: GatewayClient | None = None
        self._downloader: Downloader | None = None

    # ── Setup ────────────────────────────────────────────────────

    def setup_directories(self) -> None:
        """Creates temp directory tree for the validation run."""
        for d in [
            self._config_dir,
            self._data_dir,
            self._watch_dir,
            self._logs_dir,
            self._state_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)
        print("✅ Directories created.")

    def create_test_configs(self) -> None:
        """Writes gateway.json, paths.json, and ticker_map.json."""
        # gateway.json
        gateway_cfg = {
            "live":  {"host": GATEWAY_HOST, "port": 4001},
            "paper": {"host": GATEWAY_HOST, "port": GATEWAY_PORT},
            "mode":  GATEWAY_MODE,
        }
        with open(self._config_dir / "gateway.json", "w") as f:
            json.dump(gateway_cfg, f, indent=2)

        # paths.json (absolute paths so it works from any cwd)
        paths_cfg = {
            "parquet_dir": str(self._data_dir),
            "watch_dir":   str(self._watch_dir),
        }
        with open(self._config_dir / "paths.json", "w") as f:
            json.dump(paths_cfg, f, indent=2)

        # ticker_map.json
        ticker_map = {
            t: {"symbol": t, "exchange": "SMART", "currency": "USD", "sec_type": "STK"}
            for t in TEST_TICKERS
        }
        with open(self._config_dir / "ticker_map.json", "w") as f:
            json.dump(ticker_map, f, indent=2)

        print(f"✅ Config files written to {self._config_dir}")

    def create_watch_file(self) -> None:
        """Creates a .txt watch file containing all TEST_TICKERS."""
        watch_file = self._watch_dir / "test_validation.txt"
        watch_file.write_text(" ".join(TEST_TICKERS) + "\n", encoding="utf-8")
        print(f"✅ Watch file created: {watch_file.name} ({', '.join(TEST_TICKERS)})")

    # ── Pipeline ─────────────────────────────────────────────────

    async def run_pipeline(self) -> None:
        """
        Instantiates all components, runs startup checks,
        connects to gateway, processes the watch file, drains the queue.
        """
        print("\n── Wiring components…")
        config       = ConfigLoader(str(self._config_dir), str(self._data_dir))
        paths        = config.get_paths_config()
        writer       = ParquetWriter(str(self._data_dir))
        failed_store = FailedTickerStore(str(self._state_dir / "failed_ticker.json"))
        resolver     = TickerResolver(config, failed_store)
        queue        = DownloadQueue()
        rate_limiter = AdaptiveRateLimiter()
        gateway      = GatewayClient(config.get_gateway_config())

        self._gateway = gateway
        self._downloader = Downloader(
            gateway, queue, writer, rate_limiter, config, failed_store
        )
        watcher = FileWatcher(
            str(self._watch_dir), queue, resolver, config, failed_store
        )

        # Startup checks
        print("── Running startup checks…")
        StartupChecker(paths, writer).run_all_checks()

        # Connect
        print(f"── Connecting to IB Gateway at {GATEWAY_HOST}:{GATEWAY_PORT}…")
        try:
            await gateway.connect()
        except ConnectionError as exc:
            print(f"\n❌ {exc}")
            return

        # Market data detection
        mdt = await gateway.detect_market_data_type()
        rate_limiter.configure(mdt)
        print(f"── Market data detected: {mdt.description} (concurrent={mdt.max_concurrent}, pacing={mdt.base_pacing_delay}s)")

        # Process watch file via API Endpoint
        print("── Setting up API and triggering staleness check…")
        api = create_api(queue, resolver, config, failed_store, watcher)
        
        # Find a free port dynamically
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]

        # Start uvicorn in the background for testing
        print(f"── Starting Uvicorn API server on port {free_port}…")
        cfg = uvicorn.Config(app=api, host="127.0.0.1", port=free_port, log_level="warning")
        server = uvicorn.Server(cfg)
        server_task = asyncio.create_task(server.serve())
        
        # Wait a moment for server to bind
        await asyncio.sleep(2)
        
        print("── Sending POST request to /trigger-staleness…")
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{free_port}/trigger-staleness", method="POST")
            response = await asyncio.to_thread(urllib.request.urlopen, req)
            with response:
                status_code = response.getcode()
                response_json = response.read().decode("utf-8")
                print(f"── API Response: {status_code} - {response_json}")
        except Exception as e:
            print(f"── API Error: {e}")
            
        print(f"── Queue size after API trigger: {queue.size()}")
        
        # Shutdown server gracefully
        server.should_exit = True
        await asyncio.sleep(1)

        if queue.size() == 0:
            print("⚠️  Queue is empty after scanning — nothing to download.")
            await gateway.disconnect()
            return

        # Run downloader until queue is empty
        print(f"── Starting downloads ({queue.size()} request(s))…\n")
        await self._run_until_empty(queue)

        await gateway.disconnect()
        print("\n── Pipeline complete.\n")

    async def _run_until_empty(self, queue: DownloadQueue) -> None:
        """Runs the downloader until the queue is drained."""
        if self._downloader is None:
            return
        # Patch: run loop but stop when queue is empty
        self._downloader._running = True

        async def _stop_when_empty() -> None:
            while True:
                await asyncio.sleep(2)
                if queue.size() == 0:
                    # Wait one more cycle for current download to finish
                    await asyncio.sleep(5)
                    if queue.size() == 0:
                        self._downloader.stop()  # type: ignore[union-attr]
                        return

        await asyncio.gather(
            self._downloader.run_loop(),
            _stop_when_empty(),
        )

    # ── Verification ─────────────────────────────────────────────

    def verify_results(self) -> bool:
        """
        Scans parquet output, prints sample data for each ticker.
        Returns True if all test tickers have at least one parquet with data.
        """
        print("═" * 60)
        print("  VERIFICATION — Parquet Output")
        print("═" * 60)

        results: dict[str, bool] = {}

        if not self._data_dir.exists():
            print("❌ Data directory does not exist — no downloads occurred.")
            return False

        for ticker_dir in sorted(self._data_dir.iterdir()):
            if not ticker_dir.is_dir():
                continue
            ticker = ticker_dir.name
            parquet_files = sorted(ticker_dir.glob("*.parquet"))

            if not parquet_files:
                print(f"\n❌ {ticker}: no parquet files found")
                results[ticker] = False
                continue

            ticker_ok = False
            for pfile in parquet_files:
                timeframe = pfile.stem
                try:
                    table = pq.read_table(str(pfile))
                    rows = table.num_rows
                    if rows == 0:
                        print(f"\n⚠️  {ticker}/{timeframe}: 0 rows")
                        continue

                    ticker_ok = True
                    timestamps = table.column("timestamp").to_pylist()
                    first_dt = datetime.fromtimestamp(min(timestamps), tz=timezone.utc).strftime("%Y-%m-%d")
                    last_dt  = datetime.fromtimestamp(max(timestamps), tz=timezone.utc).strftime("%Y-%m-%d")

                    print(f"\n{'═'*60}")
                    print(f"  TICKER: {ticker} | TIMEFRAME: {timeframe}")
                    print(f"  Rows: {rows} | Date Range: {first_dt} → {last_dt}")
                    print(f"{'═'*60}")

                    # Build display table
                    cols = ["timestamp", "open", "high", "low", "close", "volume"]
                    header = f"{'Date':<22} {'Open':>9} {'High':>9} {'Low':>9} {'Close':>9} {'Volume':>14}"
                    print(header)
                    print("-" * len(header))

                    display_indices = list(range(min(5, rows)))
                    if rows > 5:
                        display_indices += list(range(max(5, rows - 5), rows))

                    for i in display_indices:
                        if i == 5 and rows > 10:
                            print("  …")
                        row_ts = int(table.column("timestamp")[i].as_py())
                        row_dt = datetime.fromtimestamp(row_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                        o  = float(table.column("open")[i].as_py())
                        h  = float(table.column("high")[i].as_py())
                        lo = float(table.column("low")[i].as_py())
                        c  = float(table.column("close")[i].as_py())
                        v  = float(table.column("volume")[i].as_py())
                        print(f"{row_dt:<22} {o:>9.2f} {h:>9.2f} {lo:>9.2f} {c:>9.2f} {v:>14.0f}")

                except Exception as exc:
                    print(f"\n❌ Error reading {pfile}: {exc}")

            results[ticker] = ticker_ok

        # Summary
        print(f"\n{'═'*60}")
        print("  VALIDATION SUMMARY")
        print(f"{'═'*60}")
        ok_count = 0
        for ticker in TEST_TICKERS:
            is_ok = results.get(ticker, False)
            icon = "✅" if is_ok else "❌"
            status = "has data" if is_ok else "NO DATA"
            print(f"  {icon} {ticker:<8} — {status}")
            if is_ok:
                ok_count += 1

        total = len(TEST_TICKERS)
        print(f"\n  Result: {ok_count}/{total} tickers OK")
        print(f"{'═'*60}\n")
        return ok_count == total

    # ── Cleanup ──────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Removes the _validation_tmp directory."""
        if os.environ.get("AUTO_CLEANUP") == "1":
            shutil.rmtree(VALIDATION_DIR, ignore_errors=True)
            print(f"✅ Auto-deleted {VALIDATION_DIR} (AUTO_CLEANUP=1)")
            return
            
        answer = input(f"Delete validation data at {VALIDATION_DIR}? (y/n): ").strip().lower()
        if answer == "y":
            shutil.rmtree(VALIDATION_DIR, ignore_errors=True)
            print(f"✅ Deleted {VALIDATION_DIR}")
        else:
            print(f"Kept {VALIDATION_DIR}")


# ─── Logging ─────────────────────────────────────────────────────

def configure_logging() -> None:
    """Simple verbose logging for the validation session."""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
    logging.basicConfig(level=logging.DEBUG, format=fmt, datefmt="%H:%M:%S")
    # Reduce ib_insync noise
    logging.getLogger("ib_insync").setLevel(logging.WARNING)


# ─── Entrypoint ──────────────────────────────────────────────────

async def main() -> None:
    configure_logging()

    print("\n" + "═" * 60)
    print("  Stock Data Node — Pipeline Validation")
    print("═" * 60 + "\n")

    validator = PipelineValidator()

    try:
        validator.setup_directories()
        validator.create_test_configs()
        validator.create_watch_file()
        await validator.run_pipeline()
        validator.verify_results()
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user.")
        if validator._gateway and validator._gateway.is_connected():
            await validator._gateway.disconnect()
        print("Current partial results:")
        validator.verify_results()
    except Exception as exc:
        print(f"\n❌ Unexpected error: {exc}")
        raise
    finally:
        try:
            validator.cleanup()
        except EOFError:
            # Non-interactive environment (e.g. piped stdin) — skip prompt
            pass


if __name__ == "__main__":
    asyncio.run(main())
