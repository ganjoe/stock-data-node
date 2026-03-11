import logging
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Optional
from dataclasses import dataclass

from features.config_parser import FeatureConfig, ProcessingContext
from features.calculator import TechnicalCalculator
from features.parquet_io import ParquetStorage

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class TickerProcessResult:
    """Result of processing a single ticker."""
    ticker: str
    timeframe: str
    success: bool
    data_points: int = 0
    error_message: Optional[str] = None

class FeatureProcessor:
    def __init__(self, context: ProcessingContext, storage: ParquetStorage, calculator: TechnicalCalculator):
        self.context = context
        self.storage = storage
        self.calculator = calculator

    def process_all_tickers(self, tickers: List[str]) -> List[TickerProcessResult]:
        """Spawns parallel processes to compute features for all tickers."""
        import time
        start_time = time.perf_counter()
        results = []
        total_tickers = len(tickers)
        completed = 0
        last_logged_pct = 0
        
        # Parallel execution on Ticker level
        with ProcessPoolExecutor(max_workers=self.context.thread_count) as executor:
            future_to_ticker = {executor.submit(self._process_single_ticker, ticker): ticker for ticker in tickers}
            
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                completed += 1
                try:
                    ticker_results = future.result()
                    results.extend(ticker_results)
                except Exception as exc:
                    logger.error(f"Ticker {ticker} generated an exception: {exc}")
                    logger.error(traceback.format_exc())
                    # Add error result for overall tracking
                    results.append(TickerProcessResult(ticker, "all", False, 0, str(exc)))
                
                # Progress logging (roughly every 10%)
                pct = int((completed / total_tickers) * 100)
                if pct - last_logged_pct >= 10 or completed == total_tickers:
                    logger.info(f"⚙️  Feature processing: {pct}% ({completed}/{total_tickers} tickers)")
                    last_logged_pct = pct

        elapsed = time.perf_counter() - start_time
        successful_results = [r for r in results if r.success]
        failed_results = [r for r in results if not r.success]
        
        total_pts = sum(r.data_points for r in successful_results)
        num_features = len(self.context.features) if hasattr(self.context, 'features') and self.context.features else 0
        
        logger.info("═══════════════════════════════════════════════════════════════")
        logger.info(f"✨ Feature Calculation Summary:")
        logger.info(f"   • Tickers processed : {total_tickers}")
        logger.info(f"   • Data points       : {total_pts:,d}".replace(",", "."))
        logger.info(f"   • Features per point: {num_features}")
        logger.info(f"   • Duration          : {elapsed:.2f}s")
        if failed_results:
            logger.warning(f"   • Failed tickers    : {len(failed_results)}")
        logger.info("═══════════════════════════════════════════════════════════════")
                    
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
                
                pts = len(df_with_features) if df_with_features is not None else 0
                ticker_results.append(TickerProcessResult(ticker, tf, True, data_points=pts))
                # Removed per-ticker logging to reduce noise
                
            except Exception as e:
                error_msg = f"Error processing {ticker} [{tf}]: {str(e)}"
                logger.error(error_msg)
                ticker_results.append(TickerProcessResult(ticker, tf, False, 0, error_msg))
                
        return ticker_results
