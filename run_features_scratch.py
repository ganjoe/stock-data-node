import logging
import time
from pathlib import Path

from src.features.config_parser import FeatureConfigParser, ProcessingContext
from src.features.calculator import TechnicalCalculator
from src.features.processor import FeatureProcessor
from src.features.parquet_io import ParquetStorage

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def run_feature_pipeline():
    print("STARTING SCRIPT", flush=True)
    config_dir = "config"
    data_dir = "data/parquet"
    
    print("PARSING CONFIG", flush=True)
    config_parser = FeatureConfigParser(str(Path(config_dir) / "features.json"))
    features = config_parser.parse()
    
    print("FINISHED PARSING CONFIG", flush=True)
    
    ctx = ProcessingContext(
        thread_count=4, 
        data_dir=data_dir,
        timeframes=["1D"],
        features=features
    )
    
    storage = ParquetStorage(ctx.data_dir)
    calculator = TechnicalCalculator()
    processor = FeatureProcessor(ctx, storage, calculator)
    
    tickers = storage.get_available_tickers()
    logging.info(f"Starting feature calculation for {len(tickers)} tickers in {data_dir}")
    
    start = time.time()
    results = processor.process_all_tickers(tickers)
    success_count = sum(1 for r in results if r.success)
    logging.info(f"Feature calculation finished: {success_count}/{len(results)} successful in {time.time() - start:.2f}s")

if __name__ == "__main__":
    run_feature_pipeline()
