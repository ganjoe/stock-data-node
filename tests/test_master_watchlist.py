import json
import os
import shutil
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# Add src to sys.path
import sys
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
if src_path not in sys.path:
    sys.path.append(src_path)

from downloader import Downloader
from models import PathsConfig

class TestMasterWatchlist(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.parquet_dir = Path(self.test_dir) / "parquet"
        self.watchlist_dir = Path(self.test_dir) / "watchlists"
        self.parquet_dir.mkdir()
        self.watchlist_dir.mkdir()
        
        # Mock dependencies
        self.gateway = MagicMock()
        self.queue = MagicMock()
        self.writer = MagicMock()
        self.rate_limiter = MagicMock()
        self.failed_store = MagicMock()
        self.config = MagicMock()
        
        # Mock paths config
        self.config.get_paths_config.return_value = PathsConfig(
            parquet_dir=str(self.parquet_dir),
            watch_dir=str(Path(self.test_dir) / "watch"),
            watchlist_dir=str(self.watchlist_dir)
        )
        
        self.downloader = Downloader(
            self.gateway, self.queue, self.writer, self.rate_limiter, self.config, self.failed_store
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_update_master_watchlist_success(self):
        # Create dummy tickers
        (self.parquet_dir / "AAPL").mkdir()
        (self.parquet_dir / "TSLA").mkdir()
        (self.parquet_dir / "MSFT").mkdir()
        (self.parquet_dir / "info.txt").write_text("ignore")
        
        self.downloader._update_master_watchlist()
        
        all_txt = self.watchlist_dir / "all.txt"
        self.assertTrue(all_txt.exists())
        
        with open(all_txt, "r") as f:
            data = f.read().splitlines()
        
        self.assertEqual(data, ["AAPL", "MSFT", "TSLA"])

    def test_update_master_watchlist_atomic(self):
        (self.parquet_dir / "AAPL").mkdir()
        
        # First run
        self.downloader._update_master_watchlist()
        
        # Second run with more tickers
        (self.parquet_dir / "NVDA").mkdir()
        self.downloader._update_master_watchlist()
        
        all_txt = self.watchlist_dir / "all.txt"
        with open(all_txt, "r") as f:
            data = f.read().splitlines()
        
        self.assertEqual(data, ["AAPL", "NVDA"])

if __name__ == "__main__":
    unittest.main()
