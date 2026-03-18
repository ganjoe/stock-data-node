import unittest
import pandas as pd
import numpy as np
import tempfile
from pathlib import Path

from src.features.config_parser import FeatureConfig, FeatureType, ProcessingContext
from src.features.calculator import TechnicalCalculator
from src.features.processor import FeatureProcessor
from src.features.parquet_io import ParquetStorage


class TestIBDRsRating(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for parquet data
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        
        self.tickers = ["AAPL", "MSFT", "GOOG", "AMZN", "META"]
        self.dates = pd.date_range(start="2022-01-01", periods=300, freq="B")
        
        # We need mock price trends to test the relative strength
        # Meta performs best, AAPL performs worst
        trends = {
            "META": 1.05,  # 5% daily growth
            "AMZN": 1.02,
            "GOOG": 1.01,
            "MSFT": 1.00,
            "AAPL": 0.99   # 1% daily loss
        }
        
        for ticker in self.tickers:
            ticker_dir = self.base_dir / ticker
            ticker_dir.mkdir(parents=True)
            
            # Generate dummy dataframe with timestamp column (Unix epoch)
            timestamps = pd.to_datetime(self.dates).astype('int64') // 10**9
            
            # cumulative product to simulate compound growth
            base_price = 100.0
            daily_returns = np.repeat(trends[ticker], len(self.dates))
            prices = base_price * np.cumprod(daily_returns)
            
            df = pd.DataFrame({
                'timestamp': timestamps.values,
                'close': prices,
                'high': prices * 1.01,
                'low': prices * 0.99,
                'open': prices,
                'volume': 1000000
            })
            
            # Save raw parquet
            df.to_parquet(ticker_dir / "1D.parquet")
        
        self.config = FeatureConfig(
            feature_id="ibd_rs",
            feature_type=FeatureType.IBD_RS,
            window=None,
            period="D",
            additional_params={}
        )
        
        self.context = ProcessingContext(
            thread_count=1, # Keep single threaded to simplify
            data_dir=str(self.base_dir),
            timeframes=["1D"],
            features=[self.config]
        )
        
        self.storage = ParquetStorage(str(self.base_dir))
        self.calculator = TechnicalCalculator()
        self.processor = FeatureProcessor(self.context, self.storage, self.calculator)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ibd_rs_calculation(self):
        # 1. Process all tickers (triggers both passes)
        results = self.processor.process_all_tickers(self.tickers)
        
        # Verify success
        for r in results:
            self.assertTrue(r.success, f"Ticker {r.ticker} failed: {r.error_message}")
            
        # 2. Check the output parquet files for the ibd_rs column and no ibd_rs_raw
        for ticker in self.tickers:
            df = self.storage.load_ticker_data(ticker, "1D_features")
            self.assertIn("ibd_rs", df.columns, f"ibd_rs missing from {ticker}")
            self.assertNotIn("ibd_rs_raw", df.columns, f"raw data not stripped from {ticker}")
            
        # 3. Verify ordering at the last row
        meta_df = self.storage.load_ticker_data("META", "1D_features")
        aapl_df = self.storage.load_ticker_data("AAPL", "1D_features")
        
        last_meta_rs = meta_df.iloc[-1]["ibd_rs"]
        last_aapl_rs = aapl_df.iloc[-1]["ibd_rs"]
        
        # Meta should be the best (99 or highest possible rank)
        # AAPL should be the worst (1 or lowest possible rank)
        
        self.assertEqual(last_meta_rs, 99)
        self.assertEqual(last_aapl_rs, 1) # Because 5 tickers, lowest is 1 if using (rank * 100).round().clip(1, 99)?
