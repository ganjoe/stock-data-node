"""
startup_checks.py — T-011
Parquet corruption check and auto-recovery on startup. (F-SYS-020, F-SYS-030)
"""
from __future__ import annotations

import logging
from pathlib import Path

from models import IParquetWriter, PathsConfig

logger = logging.getLogger(__name__)


class StartupChecker:
    """
    Runs integrity checks at container/application startup.
    - Scans all parquet files for corruption
    - Deletes corrupt files
    - Writes affected tickers to a recovery watch file for re-download
    """

    def __init__(self, paths: PathsConfig, writer: IParquetWriter) -> None:
        self._paths = paths
        self._writer = writer

    def run_all_checks(self) -> list[str]:
        """
        Runs all startup integrity checks.
        Returns list of tickers that were found corrupt and need re-download.
        """
        logger.info("═══ Running startup integrity checks… ═══")
        corrupt_tickers = self._check_parquet_integrity()
        if corrupt_tickers:
            logger.warning(
                "%d ticker(s) had corrupt parquet files and were flagged for re-download: %s",
                len(corrupt_tickers), corrupt_tickers
            )
            self._write_recovery_watch_file(corrupt_tickers)
        else:
            logger.info("✅ All parquet files OK — no corruption detected.")
        logger.info("═══ Startup checks complete. ═══")
        return corrupt_tickers

    def _check_parquet_integrity(self) -> list[str]:
        """
        Walks parquet_dir, validates every .parquet file.
        Deletes corrupt files, returns list of affected tickers (deduplicated).
        """
        parquet_dir = Path(self._paths.parquet_dir)
        if not parquet_dir.exists():
            logger.info("Parquet directory does not exist yet — skipping integrity check.")
            return []

        corrupt_tickers: set[str] = set()

        for parquet_file in parquet_dir.rglob("*.parquet"):
            # Skip temp files from incomplete writes
            if parquet_file.suffix == ".tmp":
                logger.warning("Found stale temp file %s — deleting.", parquet_file)
                parquet_file.unlink(missing_ok=True)
                continue

            is_valid = self._writer.validate_parquet(str(parquet_file))
            if not is_valid:
                logger.error("CORRUPT: %s — deleting.", parquet_file)
                try:
                    parquet_file.unlink()
                except OSError as exc:
                    logger.error("Could not delete corrupt file %s: %s", parquet_file, exc)
                # Extract ticker from directory structure: <parquet_dir>/<TICKER>/timeframe.parquet
                try:
                    ticker = parquet_file.parent.name
                    corrupt_tickers.add(ticker)
                except Exception:
                    pass

        return sorted(corrupt_tickers)

    def _write_recovery_watch_file(self, tickers: list[str]) -> None:
        """
        Writes the corrupted tickers into a recovery watch file so they are
        automatically re-downloaded by the file watcher.
        """
        watch_dir = Path(self._paths.watch_dir)
        watch_dir.mkdir(parents=True, exist_ok=True)
        recovery_file = watch_dir / "_recovery.txt"

        content = "\n".join(tickers) + "\n"
        with open(recovery_file, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(
            "Recovery file written to %s with %d ticker(s): %s",
            recovery_file, len(tickers), tickers
        )
