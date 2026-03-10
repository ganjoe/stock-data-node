import logging
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Optional
from dataclasses import dataclass

from src.features.config_parser import FeatureConfig, ProcessingContext
from src.features.calculator import TechnicalCalculator
from src.features.parquet_io import ParquetStorage

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class TickerProcessResult:
    """Result of processing a single ticker."""
    ticker: str
    timeframe: str
    success: bool
    error_message: Optional[str] = None

class FeatureProcessor:
    def __init__(self, context: ProcessingContext, storage: ParquetStorage, calculator: TechnicalCalculator):
        self.context = context
        self.storage = storage
        self.calculator = calculator

    def process_all_tickers(self, tickers: List[str]) -> List[TickerProcessResult]:
        """Spawns parallel processes to compute features for all tickers."""
        results = []
        
        # Parallel execution on Ticker level
        with ProcessPoolExecutor(max_workers=self.context.thread_count) as executor:
            future_to_ticker = {executor.submit(self._process_single_ticker, ticker): ticker for ticker in tickers}
            
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    ticker_results = future.result()
                    results.extend(ticker_results)
                except Exception as exc:
                    logger.error(f"Ticker {ticker} generated an exception: {exc}")
                    logger.error(traceback.format_exc())
                    # Add error result for overall tracking
                    results.append(TickerProcessResult(ticker, "all", False, str(exc)))
                    
        return results

    def _process_single_ticker(self, ticker: str) -> List[TickerProcessResult]:
        """The atomic unit of work executed by worker threads/processes."""
        ticker_results = []
        
        for tf in self.context.timeframes:
            try:
                # 1. Load Data
                df = self.storage.load_ticker_data(ticker, tf)
                
                # 2. Calculate Features
                df_with_features = self.calculator.calculate_features(df, self.context.features)
                
                # 3. Save Data
                self.storage.save_ticker_features(ticker, tf, df_with_features)
                
                ticker_results.append(TickerProcessResult(ticker, tf, True))
                logger.info(f"Successfully processed features for {ticker} [{tf}]")
                
            except Exception as e:
                error_msg = f"Error processing {ticker} [{tf}]: {str(e)}"
                logger.error(error_msg)
                ticker_results.append(TickerProcessResult(ticker, tf, False, error_msg))
                
        return ticker_results
