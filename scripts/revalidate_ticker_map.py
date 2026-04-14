import json
import os
import sys
from pathlib import Path

# Add src to path to import models if needed (though we'll stick to raw JSON for simplicity here)
sys.path.append(str(Path(__file__).parent.parent / "src"))

CONFIG_PATH = Path(__file__).parent.parent / "config" / "ticker_map.json"

LEVERAGED_KEYWORDS = [
    "2X", "3X", "LEVERAGED", "INVERSE", "SHORT", "ULTRA", "BULL", "BEAR", "DAILY TARGET"
]

def revalidate():
    if not CONFIG_PATH.exists():
        print(f"Error: {CONFIG_PATH} not found.")
        return

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        ticker_map = json.load(f)

    mismatches = []
    leveraged_suspects = []
    
    for ticker, contract in ticker_map.items():
        if contract is None or contract == "SKIP":
            continue
            
        symbol = contract.get("symbol")
        
        # 1. Check for symbol mismatch
        if ticker != symbol:
            mismatches.append((ticker, symbol, contract.get("exchange")))
            
        # 2. Check for leveraged ETF keywords (often a sign of auto-discovery error)
        # We don't have the "name" in ticker_map, but we can flag those where symbol contains keywords
        # or just the mismatch cases.
        if symbol:
            for kw in LEVERAGED_KEYWORDS:
                if kw in symbol.upper():
                    leveraged_suspects.append((ticker, symbol, contract.get("exchange"), f"Symbol contains {kw}"))
                    break

    print(f"\n=== Revalidation Results for {CONFIG_PATH} ===\n")
    
    if mismatches:
        print(f"Found {len(mismatches)} Symbol Mismatches (Ticker Key != Contract Symbol):")
        for t, s, e in mismatches:
            print(f"  - {t} -> {s} (Exchange: {e})")
            
        # Save to false_ticker.json (Requested by user)
        output_path = Path(__file__).parent.parent / "false_ticker.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(mismatches, f, indent=2)
        print(f"\n✅ All mismatches saved to {output_path}")
    else:
        print("No direct symbol mismatches found.")
        
    print("\n" + "="*50 + "\n")
    
    if leveraged_suspects:
        print(f"Found {len(leveraged_suspects)} Leveraged/Suspect Symbols:")
        for t, s, e, r in leveraged_suspects:
            print(f"  - {t} ({s}) on {e}: {r}")
    else:
        print("No obvious leveraged suspects found in symbols.")

if __name__ == "__main__":
    revalidate()
