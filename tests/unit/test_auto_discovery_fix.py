import sys
import os
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent))

import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock
from dataclasses import dataclass
from typing import Optional, List

# Mocking the dependencies to test Downloader._auto_discover_contract
@dataclass
class MockContract:
    symbol: str
    exchange: str
    primaryExchange: str
    currency: str
    secType: str

@dataclass
class MockContractDescription:
    contract: MockContract

@dataclass
class MockAutoDiscoveryConfig:
    currency_priority: List[str]
    exchange_priority: List[str]

@dataclass
class MockIBKRContract:
    symbol: str
    exchange: str
    currency: str
    sec_type: str

class TestAutoDiscoveryScoring(unittest.IsolatedAsyncioTestCase):
    async def test_scoring_prioritizes_exact_symbol(self):
        # 1. Setup mocks
        config_mock = MagicMock()
        auto_cfg = MockAutoDiscoveryConfig(
            currency_priority=["USD"],
            exchange_priority=["NASDAQ", "NYSE"] # NASDAQ is higher priority
        )
        config_mock.get_auto_discovery_config.return_value = auto_cfg
        
        gateway_mock = AsyncMock()
        # Simulate search for "ORCL" returning:
        # 1. ORCX on NASDAQ (Higher exchange priority)
        # 2. ORCL on NYSE (Lower exchange priority, but exact symbol match)
        gateway_mock.search_contract.return_value = [
            MockContractDescription(contract=MockContract(
                symbol="ORCX", exchange="NASDAQ", primaryExchange="NASDAQ", currency="USD", secType="STK"
            )),
            MockContractDescription(contract=MockContract(
                symbol="ORCL", exchange="NYSE", primaryExchange="NYSE", currency="USD", secType="STK"
            ))
        ]
        
        # We need a minimal Downloader or the function itself
        # Since Downloader is a class, we'll mock the rest of it
        from src.downloader import Downloader
        
        downloader = Downloader(
            gateway=gateway_mock,
            queue=MagicMock(),
            writer=MagicMock(),
            rate_limiter=MagicMock(),
            config=config_mock,
            failed_store=MagicMock()
        )
        
        # 2. Execute
        result = await downloader._auto_discover_contract("ORCL")
        
        # 3. Verify
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol, "ORCL", "Should have picked ORCL (exact match) over ORCX (higher exchange priority)")
        self.assertEqual(result.exchange, "NYSE")
        print("\n✅ Verification Success: Exact symbol match prioritized over exchange priority.")

if __name__ == "__main__":
    unittest.main()
