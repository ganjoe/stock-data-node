"""
file_watcher.py — T-007
Monitors the watch directory for .txt files, parses tickers, enqueues, cleans up.
(F-INT-020, F-INT-030)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

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

# Regex that splits on any combination of commas, spaces, or newlines
TICKER_SPLIT_RE = re.compile(r"[,\s]+")


class FileWatcher:
    """
    Periodically scans the watch directory for .txt files.
    Each file is expected to contain ticker symbols separated by whitespace, commas, or newlines.
    Successfully resolved tickers are enqueued; failed ones go to the blacklist.
    Processed files are deleted; files with unresolvable tickers have those removed.
    """

    def __init__(
        self,
        watch_dir: str,
        queue: IPriorityQueue,
        resolver: ITickerResolver,
        config: IConfigLoader,
        failed_store: IFailedTickerStore,
    ) -> None:
        self._watch_dir = Path(watch_dir)
        self._queue = queue
        self._resolver = resolver
        self._config = config
        self._failed_store = failed_store

    def scan_once(self) -> None:
        """
        Scans the watch directory for .txt files and processes each one.
        Called periodically by the main loop.
        """
        if not self._watch_dir.exists():
            return

        txt_files = sorted(self._watch_dir.glob("*.txt"))
        if not txt_files:
            return

        logger.info("File watcher: found %d file(s) in watch dir", len(txt_files))

        for filepath in txt_files:
            try:
                self._process_file(filepath)
            except FileNotFoundError:
                logger.debug("Watch file %s disappeared during processing — skipping.", filepath)
            except Exception as exc:
                logger.error("Error processing watch file %s: %s", filepath, exc)

    def _process_file(self, filepath: Path) -> None:
        """Parses a single watch file, resolves tickers, enqueues and cleans up."""
        tickers = self._parse_ticker_file(filepath)

        if not tickers:
            logger.info("ℹ️  Deleting empty watch file %s", filepath.name)
            filepath.unlink(missing_ok=True)
            return

        logger.info("Processing watch file %s with %d ticker(s)", filepath.name, len(tickers))

        failed_tickers: list[str] = []
        enqueued_tickers: list[str] = []

        for ticker in tickers:
            if self._resolver.is_ignored(ticker):
                logger.debug("Skipping unresolved/blacklisted ticker %s from file", ticker)
                continue
                
            contract = self._resolver.resolve(ticker)
            # Proceed even if contract is None (triggering Auto-Discovery in downloader)

            # Get timeframes for this ticker
            timeframes = self._config.get_timeframes_for_ticker(ticker)

            # Enqueue daily first (F-FNC-060), then other timeframes
            daily_tfs = [tf for tf in timeframes if tf == "1D"]
            other_tfs = [tf for tf in timeframes if tf != "1D"]
            ordered_tfs = daily_tfs + other_tfs

            for tf in ordered_tfs:
                req = DownloadRequest(
                    ticker=ticker,
                    timeframe=tf,
                    priority=DownloadPriority.WATCHER,
                    contract=contract,
                )
                self._queue.enqueue(req)

            enqueued_tickers.append(ticker)

        logger.info(
            "Enqueued %d ticker(s), %d failed, from %s",
            len(enqueued_tickers), len(failed_tickers), filepath.name
        )

        # Cleanup (F-INT-030)
        self._cleanup_file(filepath, failed_tickers=failed_tickers, all_tickers=tickers)

    def _parse_ticker_file(self, filepath: Path) -> list[str]:
        """
        Reads a .txt file and splits its content into ticker strings.
        Tolerates any combination of spaces, commas, and newlines as delimiters.
        """
        try:
            content = filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []

        raw_tokens = TICKER_SPLIT_RE.split(content.strip())
        tickers = [t.strip().upper() for t in raw_tokens if t.strip()]
        return tickers

    def _cleanup_file(
        self,
        filepath: Path,
        *,
        failed_tickers: list[str],
        all_tickers: list[str],
    ) -> None:
        """
        If all tickers were processed (none failed) → delete the file.
        If some tickers failed → rewrite the file with only the failed ones
        so the user can inspect and fix them manually.
        """
        if not failed_tickers:
            logger.debug("Deleting processed watch file: %s", filepath.name)
            filepath.unlink(missing_ok=True)
        else:
            # Rewrite file with only the unresolvable tickers
            remaining = "\n".join(failed_tickers) + "\n"
            filepath.write_text(remaining, encoding="utf-8")
            logger.info(
                "Watch file %s rewritten with %d unresolvable tickers: %s",
                filepath.name, len(failed_tickers), failed_tickers
            )
