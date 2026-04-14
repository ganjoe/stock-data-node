import json
import logging
import shutil
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | INFO     | cleanup              | %(message)s'
)
logger = logging.getLogger(__name__)

def cleanup_corrupted_data():
    """
    Reads false_ticker.json and deletes corresponding directories in data/parquet/.
    This removes charts that were incorrectly mapped during past auto-discovery runs.
    """
    false_ticker_file = Path("false_ticker.json")
    parquet_base_dir = Path("data/parquet")

    if not false_ticker_file.exists():
        logger.error(f"❌ {false_ticker_file} not found. Aborting.")
        return

    try:
        with open(false_ticker_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"❌ Failed to load {false_ticker_file}: {e}")
        return

    logger.info(f"▶️ Starting cleanup for {len(data)} potential entries...")
    
    deleted_count = 0
    skipped_count = 0
    error_count = 0

    for entry in data:
        # entry format: [RequestedTicker, DiscoveredTicker, Exchange]
        if not isinstance(entry, list) or len(entry) < 1:
            continue
            
        ticker = entry[0].strip().upper()
        ticker_dir = parquet_base_dir / ticker

        if ticker_dir.exists() and ticker_dir.is_dir():
            try:
                logger.info(f"🗑️ Deleting corrupted directory: {ticker_dir}")
                shutil.rmtree(ticker_dir)
                deleted_count += 1
            except Exception as e:
                logger.error(f"❌ Failed to delete {ticker_dir}: {e}")
                error_count += 1
        else:
            skipped_count += 1

    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info(f"✅ Cleanup Complete:")
    logger.info(f"   - Deleted: {deleted_count}")
    logger.info(f"   - Skipped (not found): {skipped_count}")
    logger.info(f"   - Errors: {error_count}")
    logger.info("═══════════════════════════════════════════════════════════════")

if __name__ == "__main__":
    cleanup_corrupted_data()
