"""
run_logger.py — T-IMP-004
Structured JSON run log with crash-safe writes. (F-IMP-050, F-IMP-120)
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Set

from models import ErrorCategory

logger = logging.getLogger(__name__)


class RunLogger:
    """
    Collects errors and successes during a bulk download run.
    Writes a structured JSON log file, updated after every batch.
    Designed to be used in a try/finally block for crash safety.
    """

    def __init__(self, log_dir: str) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / "run_log.json"
        self._start_time = datetime.now()

        # {ErrorCategory: {ticker: [{"message": str, "context": str}]}}
        self.errors: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        # {ticker: total_bars_fetched}
        self.successes: Dict[str, int] = defaultdict(int)
        # Unique symbols that had errors
        self._failed_symbols: Set[str] = set()

    def record_error(
        self,
        ticker: str,
        category: ErrorCategory,
        message: str,
        context: str = "",
    ) -> None:
        """Records a categorized error for a ticker."""
        self.errors[category.value][ticker].append({
            "message": message,
            "context": context,
        })
        self._failed_symbols.add(ticker)

    def record_success(self, ticker: str, bars_count: int) -> None:
        """Records a successful fetch for a ticker."""
        self.successes[ticker] += bars_count

    def write(self) -> str:
        """
        Writes/overwrites the log file. Returns the file path.
        MUST be called in a finally block for crash safety (F-IMP-120).
        """
        duration = (datetime.now() - self._start_time).total_seconds()

        # Build categorized error summary
        categorized = {}
        for category, tickers in self.errors.items():
            categorized[category] = {
                "count": len(tickers),
                "symbols": sorted(tickers.keys()),
                "details": {sym: entries for sym, entries in sorted(tickers.items())},
            }

        log_data = {
            "run_info": {
                "timestamp": self._start_time.isoformat(),
                "duration_seconds": round(duration, 1),
                "total_symbols_attempted": len(self.successes) + len(self._failed_symbols),
                "total_success": len(self.successes),
                "total_failed": len(self._failed_symbols),
                "total_bars_fetched": sum(self.successes.values()),
            },
            "errors_by_category": categorized,
            "failed_symbols": sorted(self._failed_symbols),
            "successful_symbols": sorted(self.successes.keys()),
        }

        try:
            with open(self._log_path, "w", encoding="utf-8") as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("Failed to write run log to %s: %s", self._log_path, exc)

        return str(self._log_path)

    def print_summary(self) -> None:
        """Prints a colorful summary to the terminal."""
        logger.info("═" * 60)
        logger.info("  📊 Download Run Summary")
        logger.info("═" * 60)
        logger.info(
            "  ✅ Success: %d symbols (%d bars total)",
            len(self.successes),
            sum(self.successes.values()),
        )
        logger.info("  ❌ Failed:  %d symbols", len(self._failed_symbols))

        if self.errors:
            logger.info("  Error Breakdown:")
            _emoji = {
                "QUALIFY_FAILED": "🔍",
                "NO_DATA": "📭",
                "NO_PERMISSIONS": "🔒",
                "TIMEOUT": "⏱️",
                "PACING": "🐌",
                "CANCELLED": "🚫",
                "UNKNOWN": "❓",
            }
            for cat, tickers in sorted(self.errors.items()):
                emoji = _emoji.get(cat, "❓")
                sym_list = sorted(tickers.keys())
                logger.info("    %s %s: %s", emoji, cat, ", ".join(sym_list))

        duration = (datetime.now() - self._start_time).total_seconds()
        logger.info("  ⏱️  Duration: %.1fs", duration)
        logger.info("  📝 Log: %s", self._log_path)
        logger.info("═" * 60)
