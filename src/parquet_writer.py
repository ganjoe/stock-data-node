"""
parquet_writer.py — T-006
Atomic parquet read/write with corruption validation. (F-DAT-010, F-DAT-020, F-SYS-020)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

from models import IParquetWriter, OHLCVBar

logger = logging.getLogger(__name__)

# Canonical schema — all parquet files must conform to this
OHLCV_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),
    ("open",      pa.float64()),
    ("high",      pa.float64()),
    ("low",       pa.float64()),
    ("close",     pa.float64()),
    ("volume",    pa.float64()),
])


class ParquetWriter(IParquetWriter):
    """
    Handles all parquet I/O with atomic writes via temp-file + rename strategy.
    """

    def __init__(self, parquet_dir: str):
        self._parquet_dir = Path(parquet_dir)

    def _get_parquet_path(self, ticker: str, timeframe: str) -> Path:
        return self._parquet_dir / ticker / f"{timeframe}.parquet"

    def read_last_timestamp(self, ticker: str, timeframe: str) -> Optional[int]:
        """Returns the last (most recent) timestamp in the parquet file, or None."""
        path = self._get_parquet_path(ticker, timeframe)
        if not path.exists():
            return None
        try:
            table = pq.read_table(str(path), columns=["timestamp"])
            if table.num_rows == 0:
                return None
            timestamps = table.column("timestamp").to_pylist()
            return int(max(timestamps))
        except Exception as exc:
            logger.error("Cannot read last timestamp for %s/%s: %s", ticker, timeframe, exc)
            return None

    def append_bars(self, ticker: str, timeframe: str, bars: list[OHLCVBar]) -> None:
        """
        Appends new bars to an existing parquet file using atomic temp-file + rename.
        Deduplicates by timestamp and sorts chronologically.
        """
        if not bars:
            return

        path = self._get_parquet_path(ticker, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Convert new bars to PyArrow table
        new_table = pa.table(
            {
                "timestamp": [b.timestamp for b in bars],
                "open":      [b.open for b in bars],
                "high":      [b.high for b in bars],
                "low":       [b.low for b in bars],
                "close":     [b.close for b in bars],
                "volume":    [b.volume for b in bars],
            },
            schema=OHLCV_SCHEMA,
        )

        # Merge with existing data if present
        if path.exists():
            try:
                existing = pq.read_table(str(path))
                combined = pa.concat_tables([existing, new_table])
            except Exception as exc:
                logger.warning(
                    "Cannot read existing parquet for %s/%s: %s — overwriting",
                    ticker, timeframe, exc
                )
                combined = new_table
        else:
            combined = new_table

        # Deduplicate by timestamp and sort
        import pyarrow.compute as pc
        timestamps = combined.column("timestamp").to_pylist()
        seen: set[int] = set()
        keep_indices = []
        for i, ts in enumerate(timestamps):
            if ts not in seen:
                seen.add(ts)
                keep_indices.append(i)
        if len(keep_indices) < len(timestamps):
            combined = combined.take(keep_indices)
        # Sort by timestamp
        sort_indices = pc.sort_indices(combined, sort_keys=[("timestamp", "ascending")])
        combined = combined.take(sort_indices)

        # Atomic write: temp file → rename
        tmp_path = path.with_suffix(".parquet.tmp")
        try:
            pq.write_table(combined, str(tmp_path))
            os.replace(str(tmp_path), str(path))
            logger.info(
                "Written %s bars to %s/%s.parquet (total: %s)",
                f"{len(bars):,d}".replace(",", "."), ticker, timeframe, 
                f"{combined.num_rows:,d}".replace(",", ".")
            )
        except Exception as exc:
            logger.error("Failed to write parquet for %s/%s: %s", ticker, timeframe, exc)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

    def validate_parquet(self, filepath: str) -> bool:
        """Returns True if the parquet file can be read successfully."""
        try:
            pq.read_table(filepath)
            return True
        except Exception as exc:
            logger.warning("Corrupt parquet file %s: %s", filepath, exc)
            return False

    def check_year_coverage(self, ticker: str, timeframe: str) -> set[int]:
        """
        Scans existing parquet to find years with >200 bars (fully covered).
        Current year is always excluded from "covered" to force update. (F-IMP-030)
        """
        path = self._get_parquet_path(ticker, timeframe)
        if not path.exists():
            return set()

        try:
            table = pq.read_table(str(path), columns=["timestamp"])
            if table.num_rows == 0:
                return set()
        except Exception as exc:
            logger.warning("Cannot read coverage for %s/%s: %s", ticker, timeframe, exc)
            return set()

        from datetime import datetime

        # Count bars per year
        year_counts: dict[int, int] = {}
        for ts in table.column("timestamp").to_pylist():
            y = datetime.fromtimestamp(int(ts)).year
            year_counts[y] = year_counts.get(y, 0) + 1

        # Threshold: 200 trading days = roughly a full year
        covered = {y for y, count in year_counts.items() if count > 200}

        # Always exclude current year — force update
        current_year = datetime.now().year
        covered.discard(current_year)

        return covered
