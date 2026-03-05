import unittest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path
from datetime import datetime
import pytz

SRC_DIR = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from market_clock import MarketClock
from models import MarketStatus

class TestMarketClock(unittest.TestCase):
    @patch('market_clock.datetime')
    def test_market_clock_holiday(self, mock_datetime):
        mock_now = datetime(2024, 1, 1, 12, 0, tzinfo=MarketClock.TIMEZONE)
        mock_datetime.now.return_value = mock_now
        
        status = MarketClock.get_status()
        self.assertEqual(status["status"], MarketStatus.CLOSED_HOLIDAY.value)

    @patch('market_clock.datetime')
    def test_market_clock_weekend(self, mock_datetime):
        mock_now = datetime(2024, 1, 6, 12, 0, tzinfo=MarketClock.TIMEZONE)
        mock_datetime.now.return_value = mock_now
        
        status = MarketClock.get_status()
        self.assertEqual(status["status"], MarketStatus.CLOSED_WEEKEND.value)

    @patch('market_clock.datetime')
    def test_market_clock_open(self, mock_datetime):
        mock_now = datetime(2024, 1, 8, 10, 0, tzinfo=MarketClock.TIMEZONE)
        mock_datetime.now.return_value = mock_now
        
        status = MarketClock.get_status()
        self.assertEqual(status["status"], MarketStatus.OPEN.value)

if __name__ == '__main__':
    unittest.main()
