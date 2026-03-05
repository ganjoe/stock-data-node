import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock
import tempfile
import json

sys.path.insert(0, os.path.abspath("src"))

from models import DownloadRequest, DownloadPriority, TickerStatus, IBKRContract, FailedTickerEntry
from downloader import Downloader, ErrorCategory
from config_loader import ConfigLoader
from failed_ticker_store import FailedTickerStore
from ticker_resolver import TickerResolver

async def run_test():
    with tempfile.TemporaryDirectory() as td:
        cf_path = os.path.join(td, "config")
        os.makedirs(cf_path)
        with open(os.path.join(cf_path, "paths.json"), "w") as f:
            json.dump({"parquet_dir": td, "watch_dir": td}, f)
        with open(os.path.join(cf_path, "gateway.json"), "w") as f:
            json.dump({"mode":"paper", "live":{"host":"127", "port":4001}, "paper":{"host":"127", "port":4002}}, f)
        with open(os.path.join(cf_path, "ticker_map.json"), "w") as f:
            json.dump({}, f)
        
        fs_path = os.path.join(td, "failed.json")
        fs = FailedTickerStore(fs_path)
        config = ConfigLoader(cf_path, td)
        resolver = TickerResolver(config, fs)
        
        # Test 1: T-EXT-001 Default Contract Inference
        contract = resolver.resolve("UNKNOWN1")
        assert contract is not None, "T-EXT-001 Failed: returned None"
        assert contract.exchange == "SMART", "T-EXT-001 Failed: default exchange wrong"
        print("✅ T-EXT-001 passed")
        
        # Mocks for downloader
        gateway = AsyncMock()
        gateway.is_connected = MagicMock(return_value=True)
        # Mock download chunk to fake a QUALIFY_FAILED error
        
        queue = MagicMock()
        queue.has_higher_priority_waiting.return_value = False
        writer = MagicMock()
        writer.read_last_timestamp.return_value = None
        rate_limiter = AsyncMock()
        
        downloader = Downloader(gateway, queue, writer, rate_limiter, config, fs)
        
        # Test 2: T-EXT-002 Defer Blacklisting to Downloader upon qualification fail
        req_fail = DownloadRequest(ticker="BADSYM", timeframe="1D", priority=DownloadPriority.WATCHER, contract=contract)
        gateway.request_historical_bars.side_effect = Exception("No security definition has been found")
        
        await downloader._process_request(req_fail)
        
        assert req_fail.status == TickerStatus.FAILED, f"Status should be FAILED, got {req_fail.status}"
        assert fs.is_blacklisted("BADSYM"), "T-EXT-002 Failed: Ticker not added to blacklist"
        
        # Check if mapped to null in ticker_map.json
        config.reload_if_changed()
        tm = config.get_ticker_map()
        assert "BADSYM" in tm and tm["BADSYM"].symbol == "SKIP", "T-EXT-002 Failed: Not mapped to SKIP sentinel"
        print("✅ T-EXT-002 passed")
        
        # Test 3: T-EXT-003 Clean up failed_ticker.json on success
        fs.add(FailedTickerEntry(ticker="RECME", reason="Manual", timestamp="2024", source="manual"))
        
        req_ok = DownloadRequest(ticker="RECME", timeframe="1D", priority=DownloadPriority.WATCHER, contract=contract)
        gateway.request_historical_bars.side_effect = None
        gateway.request_historical_bars.return_value = [("bar1",)]  # fake data
        
        await downloader._process_request(req_ok)
        assert req_ok.status == TickerStatus.DONE, f"Status should be DONE, got {req_ok.status}"
        assert not fs.is_blacklisted("RECME"), "T-EXT-003 Failed: Ticker not removed from blacklist"
        print("✅ T-EXT-003 passed")

if __name__ == "__main__":
    asyncio.run(run_test())
