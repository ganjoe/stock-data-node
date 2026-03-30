import logging
import sys
from pathlib import Path

# Add src to sys.path to allow imports
sys.path.append(str(Path.cwd() / "src"))

from src.features.config_parser import FeatureConfigParser, ProcessingContext
from src.features.calculator import TechnicalCalculator
from src.features.processor import FeatureProcessor
from src.features.parquet_io import ParquetStorage

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def test_single_ticker(ticker="AAPL"):
    config_path = "config/features.json"
    data_dir = "data/parquet"
    
    config_parser = FeatureConfigParser(config_path)
    features = config_parser.parse()
    
    ctx = ProcessingContext(
        thread_count=1, 
        data_dir=data_dir,
        timeframes=["1D"],
        features=features
    )
    
    storage = ParquetStorage(ctx.data_dir)
    calculator = TechnicalCalculator()
    processor = FeatureProcessor(ctx, storage, calculator)
    
    print(f"Starting feature calculation for {ticker}")
    results = processor.process_all_tickers([ticker])
    
    for r in results:
        if r.success:
            print(f"Success for {r.ticker}")
            import pandas as pd
            df = pd.read_parquet(f"data/parquet/{r.ticker}/1D_features.parquet")
            cols = [c for c in df.columns if "stock_14_3" in c]
            print(f"Columns found: {cols}")
        else:
            print(f"Error for {r.ticker}: {r.error_message}")

if __name__ == "__main__":
    test_single_ticker()
