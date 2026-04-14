import unittest
from unittest.mock import MagicMock, AsyncMock
import sys
from pathlib import Path

# Add src to sys.path
sys.path.append(str(Path.cwd() / "src"))

from downloader import Downloader
from models import IBKRContract, AutoDiscoveryConfig
from ib_insync import Contract, ContractDescription

class TestAutoDiscovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_gateway = MagicMock()
        self.mock_queue = MagicMock()
        self.mock_writer = MagicMock()
        self.mock_rate_limiter = MagicMock()
        self.mock_config = MagicMock()
        self.mock_failed_store = MagicMock()
        
        self.downloader = Downloader(
            self.mock_gateway,
            self.mock_queue,
            self.mock_writer,
            self.mock_rate_limiter,
            self.mock_config,
            self.mock_failed_store
        )
        
        # Default config
        self.mock_config.get_auto_discovery_config.return_value = AutoDiscoveryConfig(
            currency_priority=["USD", "EUR"],
            exchange_priority=["SMART", "NASDAQ"]
        )

    async def test_strict_match_success(self):
        """Should return the contract if an exact symbol match exists."""
        ticker = "AAPL"
        mock_results = [
            ContractDescription(contract=Contract(symbol="AAPL", secType="STK", exchange="NASDAQ", currency="USD")),
            ContractDescription(contract=Contract(symbol="AAPU", secType="STK", exchange="NASDAQ", currency="USD")), # Partial match
        ]
        self.mock_gateway.search_contract = AsyncMock(return_value=mock_results)
        
        result = await self.downloader._auto_discover_contract(ticker)
        
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol, "AAPL")

    async def test_strict_match_failure(self):
        """Should return None if only partial symbol matches exist."""
        ticker = "AGO"
        mock_results = [
            ContractDescription(contract=Contract(symbol="AGMB", secType="STK", exchange="NASDAQ", currency="USD")),
        ]
        self.mock_gateway.search_contract = AsyncMock(return_value=mock_results)
        
        result = await self.downloader._auto_discover_contract(ticker)
        
        self.assertIsNone(result, "Should return None because AGO != AGMB")

    async def test_priority_selection(self):
        """Should pick the best contract based on currency/exchange priority if multiple exact matches exist."""
        ticker = "AAPL"
        mock_results = [
            ContractDescription(contract=Contract(symbol="AAPL", secType="STK", exchange="MEXI", currency="MXN")),
            ContractDescription(contract=Contract(symbol="AAPL", secType="STK", exchange="NASDAQ", currency="USD")), # Better currency
        ]
        self.mock_gateway.search_contract = AsyncMock(return_value=mock_results)
        
        result = await self.downloader._auto_discover_contract(ticker)
        
        self.assertIsNotNone(result)
        self.assertEqual(result.currency, "USD")
        self.assertEqual(result.exchange, "NASDAQ")

if __name__ == "__main__":
    unittest.main()
