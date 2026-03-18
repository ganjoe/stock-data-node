import pandas as pd
import os
from pathlib import Path
from typing import List

import pyarrow as pa
import pyarrow.parquet as pq
import logging

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


class ParquetStorage:
    def __init__(self, base_data_dir: str):
        self.base_dir = Path(base_data_dir)

    def load_ticker_data(self, ticker: str, timeframe: str) -> pd.DataFrame:
        """Loads /data/parquet/<ticker>/<timeframe>.parquet"""
        file_path = self.base_dir / ticker / f"{timeframe}.parquet"
        
        if not file_path.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")
            
        table = pq.read_table(str(file_path))
        return table.to_pandas()

    def save_ticker_features(self, ticker: str, timeframe: str, df: pd.DataFrame) -> None:
        """Saves to /data/parquet/<ticker>/<timeframe>_features.parquet (atomic write via temp file + rename)"""
        output_dir = self.base_dir / ticker
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Convert DataFrame to PyArrow Table
        table = pa.Table.from_pandas(df, schema=OHLCV_SCHEMA, preserve_index=False)
        
        output_path = output_dir / f"{timeframe}_features.parquet"
        tmp_path = output_path.with_suffix(".parquet.tmp")
        
        try:
            pq.write_table(table, str(tmp_path))
            os.replace(str(tmp_path), str(output_path))
            logger.info(f"Written {len(df)} rows to {ticker}/{timeframe}_features.parquet")
        except Exception as exc:
            logger.error(f"Failed to write parquet for {ticker}/{timeframe}: {exc}")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
    
    def get_available_tickers(self) -> List[str]:
        """Scans the directory structure to identify which tickers have parquet data."""
        if not self.base_dir.exists():
            return []
        
        # Subdirectories in base_dir are assumed to be tickers
        return [d.name for d in self.base_dir.iterdir() if d.is_dir()]
