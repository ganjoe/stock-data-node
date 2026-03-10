# Implementation Plan: Feature Processing

## PART 1: The System Skeleton (Shared Context)

These data structures, configurations, and interfaces form the foundation for the feature calculation system. They enforce a strict separation between I/O, configuration parsing, data processing, and concurrency management. All components rely on these shared definitions to communicate safely.

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import pandas as pd

class FeatureType(str, Enum):
    SMA = "SMA"
    EMA = "EMA"
    BOLLINGER_BAND = "BOLLINGER_BAND"
    STOCHASTIC = "STOCHASTIC"
    UNKNOWN = "UNKNOWN"

@dataclass
class FeatureConfig:
    """Represents the purely functional arguments of a feature from features.json"""
    feature_id: str             # e.g., "ma_sma_10"
    feature_type: FeatureType
    window: Optional[int] = None
    period: str = "D"
    additional_params: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TickerProcessResult:
    """Result of processing a single ticker."""
    ticker: str
    timeframe: str
    success: bool
    error_message: Optional[str] = None

@dataclass
class ProcessingContext:
    """Holds shared configuration for the current run."""
    thread_count: int
    data_dir: str # Base directory containing parquet files
    timeframes: List[str] # E.g., ["1D", "1H"]
    features: List[FeatureConfig]
```

---

## PART 2: Implementation Work Orders

### Task ID: [T-001]
**Target File:** `src/features/config_parser.py`
**Description:** Extract only calculation-relevant parameters from `features.json` to create a clean list of `FeatureConfig` definitions.
**Context:** Uses `FeatureConfig`, `FeatureType`
**Code Stub (MANDATORY):**
```python
import json
from typing import List, Dict, Any
# Import FeatureConfig, FeatureType from skeleton

class FeatureConfigParser:
    def __init__(self, config_path: str):
        self.config_path = config_path
        
    def parse(self) -> List[FeatureConfig]:
        """Reads features.json and maps to FeatureConfig objects, ignoring visual properties."""
        pass
```
**Algo/Logic Steps:**
1. Open and load the JSON file from `self.config_path`.
2. Iterate over the `"features"` dictionary.
3. For each item, extract the functional fields (`type`, `window`, `period`).
4. Skip purely visual properties (`color`, `style`, `pane`, `chart_type`, `thickness`, `mode`).
5. Map the string `"type"` to the `FeatureType` enum. Keep unrecognized parameters in `additional_params`.
6. Return a list of populated `FeatureConfig` objects.
**Edge Cases:**
- Missing keys (e.g. `window` is omitted without a default) -> Set to None or sensible defaults, do not crash.

---

### Task ID: [T-002]
**Target File:** `src/features/calculator.py`
**Description:** Process the actual features mapped to specific algorithms, providing backfilling to avoid nulls. Includes stubs for non-MAs.
**Context:** Uses `FeatureConfig`, `FeatureType`
**Code Stub (MANDATORY):**
```python
import pandas as pd
from typing import List
# Import FeatureConfig, FeatureType

class TechnicalCalculator:
    def calculate_features(self, df: pd.DataFrame, configs: List[FeatureConfig]) -> pd.DataFrame:
        """Applies technical indicators to the dataframe based on provided configs."""
        pass
    
    def _backfill_data(self, df: pd.DataFrame, target_column: str, window: int) -> pd.DataFrame:
        """Backfills missing early data to ensure no NaN/0 logic by duplicating the first valid value backwards."""
        pass

    def _calc_sma(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        pass

    def _calc_ema(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        pass
        
    def _calc_stubs(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        pass
```
**Algo/Logic Steps:**
1. `calculate_features`: Loop over `configs`. Based on `feature_type`, route to `_calc_sma`, `_calc_ema`, or `_calc_stubs`.
2. `_calc_sma` / `_calc_ema`: Use pandas rolling/ewm logic to calculate the average on a suitable price column (like `close`). Name the new column `config.feature_id`.
3. `_backfill_data`: Apply filling logic. For new MA columns that generate NaNs in the initial `window` rows, fill these empty records by projecting the first valid calculated row backwards, or by projecting the first available uncalculated price row backwards before calculating (as specified by user requirement to duplicate the first day's data up to `window` times).
4. `_calc_stubs`: Simply `pass` or return `df` unmodified for BOLLINGER_BAND, STOCHASTIC.
5. Return the enhanced DataFrame.
**Edge Cases:**
- Input dataframe has fewer rows than the `window` size. Ensure the logic can duplicate the first available row enough times to perform the calculation cleanly.

---

### Task ID: [T-003]
**Target File:** `src/features/parquet_io.py`
**Description:** Abstracted file system interaction to read raw parity data and overwrite with feature parity data.
**Context:** Standard data handling.
**Code Stub (MANDATORY):**
```python
import pandas as pd
import os
from pathlib import Path

class ParquetStorage:
    def __init__(self, base_data_dir: str):
        self.base_dir = Path(base_data_dir)

    def load_ticker_data(self, ticker: str, timeframe: str) -> pd.DataFrame:
        """Loads /data/parquet/<ticker>/<timeframe>.parquet"""
        pass

    def save_ticker_features(self, ticker: str, timeframe: str, df: pd.DataFrame) -> None:
        """Saves to /data/parquet/<ticker>/<timeframe>_features.parquet (Overwrite)"""
        pass
    
    def get_available_tickers(self) -> List[str]:
        """Scans the directory structure to identify which tickers have parquet data."""
        pass
```
**Algo/Logic Steps:**
1. `load_ticker_data`: Construct path. If file exists, read parquet via `pandas.read_parquet`.
2. `save_ticker_features`: Construct output path (`_features.parquet`). Overwrite existing file via `pandas.to_parquet`. Creates directories if they do not exist.
**Edge Cases:**
- Missing input file -> Raise a custom exception or FileNotFoundError so processor can handle it gracefully.

---

### Task ID: [T-004]
**Target File:** `src/features/processor.py`
**Description:** Orchestrates the multi-core processing of multiple tickers in isolation.
**Context:** Uses `ProcessingContext`, `TickerProcessResult`, `ParquetStorage`, `TechnicalCalculator`
**Code Stub (MANDATORY):**
```python
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
# context imports

logger = logging.getLogger(__name__)

class FeatureProcessor:
    def __init__(self, context: ProcessingContext, storage: ParquetStorage, calculator: TechnicalCalculator):
        self.context = context
        self.storage = storage
        self.calculator = calculator

    def process_all_tickers(self, tickers: List[str]) -> List[TickerProcessResult]:
        """Spawns parallel processes to compute features for all tickers."""
        pass

    def _process_single_ticker(self, ticker: str) -> TickerProcessResult:
        """The atomic unit of work executed by worker threads/processes."""
        pass
```
**Algo/Logic Steps:**
1. `process_all_tickers`: Initialize a `ProcessPoolExecutor` with `max_workers=self.context.thread_count`.
2. Submit `_process_single_ticker` for each ticker.
3. Collect futures using `as_completed(futures)`. Append to a results list.
4. `_process_single_ticker`: 
    - Wrap in `try...except`.
    - Iterate through `self.context.timeframes`.
    - Load data via `storage.load_ticker_data`.
    - Apply features via `calculator.calculate_features`.
    - Save data via `storage.save_ticker_features`.
    - Return a successful `TickerProcessResult`.
5. On Exception, `logger.error` the exact stack trace to the terminal, and return a failed `TickerProcessResult` with the exception text. Do not crash the executor.
**Edge Cases:**
- The process pool crashes fundamentally. Catch and log outer executor exceptions.

---

### Task ID: [T-005]
**Target File:** `src/features/job_manager.py`
**Description:** Job Overlap Protection. Wraps the synchronous `FeatureProcessor` in a thread-safe mutex/state to prevent double executions.
**Context:** F-SYS-030
**Code Stub (MANDATORY):**
```python
import threading
import logging

logger = logging.getLogger(__name__)

class JobManager:
    def __init__(self):
        self._is_running = False
        self._lock = threading.Lock()

    def start_feature_calculation(self, run_func, *args, **kwargs) -> bool:
        """
        Attempts to start the feature calculation job. 
        Returns True if started successfully, False if skipped due to existing job.
        run_func represents the orchestrator method.
        """
        pass
```
**Algo/Logic Steps:**
1. Check state using `_lock`. If `self._is_running` is True, release lock, return False (Skip/Drop).
2. If False, set `self._is_running = True`, release lock.
3. Spawn a background thread that executes `run_func(*args, **kwargs)`.
4. Wrap the thread execution in a `try...finally` block.
5. In the `finally` block, acquire `_lock` and set `self._is_running = False` ensuring state resets regardless of success/fail.
**Edge Cases:**
- Target function throws unhandled exception -> Finally block must trigger to prevent permanent lockdown.

---

### Task ID: [T-006]
**Target File:** `src/api/routes_features.py` (or equivalent FastAPI/Flask router)
**Description:** The API Endpoint logic to manually trigger the computation.
**Context:** F-API-010, F-API-020
**Code Stub (MANDATORY):**
```python
# Assuming FastAPI architecture
from fastapi import APIRouter, BackgroundTasks, HTTPException
# import JobManager

router = APIRouter()
job_manager = JobManager() # Should become a global singleton injected dependency

@router.post("/features/calculate")
async def trigger_feature_calculation(background_tasks: BackgroundTasks):
    """API endpoint to trigger computation."""
    pass
```
**Algo/Logic Steps:**
1. When endpoint is hit, invoke `job_manager.start_feature_calculation(...)`.
2. If it returns True, return `{ "status": "Job started in background" }` HTTP 202.
3. If it returns False, return `{ "status": "Ignored", "detail": "A feature calculation process is already running." }` HTTP 409 Conflict.
4. *Integration Note:* The main Download orchestration logic must also be updated to programmatically call `job_manager.start_feature_calculation(...)` upon successful fetch batch completion.
**Edge Cases:**
- None. Logic is delegated to JobManager.
