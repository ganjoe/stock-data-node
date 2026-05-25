#!/usr/bin/env python3
"""
check_staleness.py
Standalone script to check and print the staleness distribution.
"""
import os
import sys
import time
from pathlib import Path

# Add src to path so we can import from it
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config_loader import ConfigLoader
from ticker_resolver import TickerResolver
from failed_ticker_store import FailedTickerStore
from parquet_writer import ParquetWriter

def main():
    base_dir = Path(__file__).parent.parent
    config_dir = str(base_dir / "config")
    
    print("Loading configuration...")
    # 1. Init Config
    try:
        config = ConfigLoader(config_dir=config_dir, parquet_dir="")
        paths = config.get_paths_config()
        config = ConfigLoader(config_dir=config_dir, parquet_dir=paths.parquet_dir)
    except FileNotFoundError as exc:
        print(f"Error loading config: {exc}")
        sys.exit(1)
        
    state_dir = base_dir / "state"
    failed_store = FailedTickerStore(str(state_dir / "failed_ticker.json"))
    resolver = TickerResolver(config, failed_store)
    writer = ParquetWriter(paths.parquet_dir)
    
    parquet_dir_path = Path(paths.parquet_dir)
    all_tickers = set()
    
    if parquet_dir_path.exists() and parquet_dir_path.is_dir():
        for item in parquet_dir_path.iterdir():
            if item.is_dir():
                all_tickers.add(item.name)
                
    print(f"Scanning {len(all_tickers)} ticker(s) for staleness...\n")
    
    count = 0
    age_counts = {}
    now = time.time()
    
    for ticker in all_tickers:
        if resolver.is_ignored(ticker):
            continue
            
        timeframes = config.get_timeframes_for_ticker(ticker)
        for tf in timeframes:
            last_ts = writer.read_last_timestamp(ticker, tf)
            if last_ts is None:
                last_updated = 0.0
                bucket = "No data"
            else:
                last_updated = float(last_ts)
                days_outdated = int((now - last_updated) // 86400)
                bucket = f"-{days_outdated} days"

            age_counts[bucket] = age_counts.get(bucket, 0) + 1
            count += 1

    if count == 0:
        print("No valid tickers found.")
        return
        
    print("Staleness Distribution (Sorted by Percentage):")
    print("-" * 50)
    
    # Sort by percentage (which is the same as sorting by count) descending
    sorted_buckets = sorted(age_counts.keys(), key=lambda b: age_counts[b], reverse=True)
    
    for b in sorted_buckets:
        c = age_counts[b]
        pct = (c / count) * 100
        print(f"  {pct:5.1f}% {b:<10} ({c})")
        
    print("-" * 50)
    print(f"Total evaluated: {count}")

if __name__ == "__main__":
    main()
