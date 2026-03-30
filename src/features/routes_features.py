"""
routes_features.py — Feature Calculation API Routes (F-API-010, F-SYS-030)
"""
from fastapi import FastAPI, BackgroundTasks, status
from fastapi.responses import JSONResponse

from features.job_manager import JobManager
from features.config_parser import FeatureConfigParser, ProcessingContext, FeatureType
from features.calculator import TechnicalCalculator
from features.parquet_io import ParquetStorage


def register_feature_routes(app: FastAPI) -> None:
    """
    Register feature calculation routes with the app.
    
    Routes:
        POST /features/calculate — Trigger feature calculation (F-API-010)
    """
    from pathlib import Path
    import logging
    
    logger = logging.getLogger(__name__)
    
    from fastapi.responses import StreamingResponse
    
    @app.post("/features/calculate")
    async def trigger_feature_calculation(background_tasks: BackgroundTasks, stream: bool = False):
        """
        Manually triggers the feature calculation process.
        Returns 202 if started, 409 if already running. (F-API-010, F-SYS-030)
        If stream=True, returns a StreamingResponse with real-time logs.
        """
        job_manager = JobManager()
        
        def run_feature_pipeline():
            # Use settings from config file
            from models import IConfigLoader
            
            # Import here to avoid circular dependency issues
            from config_loader import ConfigLoader
            
            # Determine config path
            base_dir = Path(__file__).parent.parent.parent
            config_dir = str(base_dir / "config")
            
            config = ConfigLoader(config_dir=config_dir, parquet_dir="")
            paths = config.get_paths_config()
            settings_cfg = config.get_settings_config()
            
            config_parser = FeatureConfigParser(str(Path(config_dir) / "features.json"))
            features = config_parser.parse()
            
            ctx = ProcessingContext(
                thread_count=settings_cfg.processing_threads,
                data_dir=paths.parquet_dir,
                timeframes=["1D"],  # Default focus
                features=features
            )
            
            storage = ParquetStorage(ctx.data_dir)
            calculator = TechnicalCalculator()
            processor = FeatureProcessor(ctx, storage, calculator)
            
            tickers = storage.get_available_tickers()
            logger.info("Starting feature calculation for %d tickers", len(tickers))
            results = processor.process_all_tickers(tickers)
            success_count = sum(1 for r in results if r.success)
            logger.info("Feature calculation finished: %d/%d successful", success_count, len(results))

        from features.processor import FeatureProcessor
        
        if stream:
            return StreamingResponse(
                job_manager.stream_feature_calculation(run_feature_pipeline),
                media_type="text/plain"
            )
        
        success = job_manager.start_feature_calculation(run_feature_pipeline)
        
        if success:
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "Job started in background",
                    "hint": "Use ?stream=true to see real-time log output in terminal (e.g. curl -N ...?stream=true)"
                }
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"status": "Ignored", "detail": "A feature calculation process is already running."}
            )
