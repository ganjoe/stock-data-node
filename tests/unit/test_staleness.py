import unittest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path
from datetime import datetime, timedelta

SRC_DIR = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from staleness import is_stale

class TestStaleness(unittest.TestCase):
    @patch('staleness.datetime')
    def test_staleness_intraday(self, mock_datetime):
        mock_now = datetime(2024, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = mock_now
        mock_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
        mock_datetime.combine.side_effect = datetime.combine
        
        last_ts = (mock_now - timedelta(seconds=61)).timestamp()
        self.assertTrue(is_stale(int(last_ts), "1m"))

        last_ts_not_stale = (mock_now - timedelta(seconds=59)).timestamp()
        self.assertFalse(is_stale(int(last_ts_not_stale), "1m"))

    @patch('staleness.datetime')
    def test_staleness_daily(self, mock_datetime):
        mock_now = datetime(2024, 1, 2, 12, 0, 0)
        mock_datetime.now.return_value = mock_now
        mock_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
        mock_datetime.combine.side_effect = datetime.combine
        
        last_ts_today = datetime(2024, 1, 2, 9, 30, 0).timestamp()
        self.assertFalse(is_stale(int(last_ts_today), "1D"))

        last_ts_yesterday = datetime(2024, 1, 1, 16, 0, 0).timestamp()
        self.assertTrue(is_stale(int(last_ts_yesterday), "1D"))

if __name__ == '__main__':
    unittest.main()
