"""
failed_ticker_store.py — T-010
Manages the failed_ticker.json blacklist. (F-FNC-070)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from models import FailedTickerEntry, IFailedTickerStore

logger = logging.getLogger(__name__)


class FailedTickerStore(IFailedTickerStore):
    """
    Manages the failed_ticker.json file.
    File is also manually editable by the user — removing an entry re-enables download.
    """

    def __init__(self, filepath: str):
        self._filepath = Path(filepath)
        self._filepath.parent.mkdir(parents=True, exist_ok=True)

    def is_blacklisted(self, ticker: str) -> bool:
        """Re-reads file from disk on every call so manual edits take effect immediately."""
        entries = self.load()
        blacklisted = {e.ticker.upper() for e in entries}
        return ticker.upper() in blacklisted

    def add(self, entry: FailedTickerEntry) -> None:
        """Appends a new entry to the JSON file."""
        entries = self.load()
        # Avoid duplicate entries for the same ticker
        existing_tickers = {e.ticker.upper() for e in entries}
        if entry.ticker.upper() in existing_tickers:
            logger.warning(
                "Ticker %s already in failed_ticker.json — skipping duplicate",
                entry.ticker
            )
            return
        entries.append(entry)
        self._write(entries)
        logger.info("Added %s to failed_ticker.json (reason: %s)", entry.ticker, entry.reason)

    def remove(self, ticker: str) -> None:
        """Removes a ticker from the blacklist if it exists."""
        entries = self.load()
        filtered = [e for e in entries if e.ticker.upper() != ticker.upper()]
        
        if len(filtered) < len(entries):
            self._write(filtered)
            logger.info("Removed %s from failed_ticker.json (successful recovery)", ticker)

    def load(self) -> list[FailedTickerEntry]:
        """Reads and parses failed_ticker.json. Returns empty list if missing or corrupt."""
        if not self._filepath.exists():
            return []
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [
                FailedTickerEntry(
                    ticker=item["ticker"],
                    reason=item["reason"],
                    timestamp=item["timestamp"],
                    source=item.get("source", "unknown"),
                )
                for item in raw
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error(
                "Failed to parse %s: %s — treating as empty blacklist",
                self._filepath, exc
            )
            return []

    def _write(self, entries: list[FailedTickerEntry]) -> None:
        data = [
            {
                "ticker": e.ticker,
                "reason": e.reason,
                "timestamp": e.timestamp,
                "source": e.source,
            }
            for e in entries
        ]
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
