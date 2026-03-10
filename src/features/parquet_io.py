import pandas as pd
import os
from pathlib import Path
from typing import List

class ParquetStorage:
    def __init__(self, base_data_dir: str):
        self.base_dir = Path(base_data_dir)

    def load_ticker_data(self, ticker: str, timeframe: str) -> pd.DataFrame:
        """Loads /data/parquet/<ticker>/<timeframe>.parquet"""
        # Note: Depending on project structure, path might be relative to base_dir or absolute
        # Assuming the structure: /data/parquet/<ticker>/<timeframe>.parquet
        file_path = self.base_dir / ticker / f"{timeframe}.parquet"
        
        if not file_path.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")
            
        return pd.read_parquet(file_path)

    def save_ticker_features(self, ticker: str, timeframe: str, df: pd.DataFrame) -> None:
        """Saves to /data/parquet/<ticker>/<timeframe>_features.parquet (Overwrite)"""
        output_dir = self.base_dir / ticker
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = output_dir / f"{timeframe}_features.parquet"
        df.to_parquet(output_path, index=False)
    
    def get_available_tickers(self) -> List[str]:
        """Scans the directory structure to identify which tickers have parquet data."""
        if not self.base_dir.exists():
            return []
        
        # Subdirectories in base_dir are assumed to be tickers
        return [d.name for d in self.base_dir.iterdir() if d.is_dir()]
