# Implementation Plan: Parquet Infrastructure

## Overview
This document outlines the schema, storage structure, atomic write operations, and corruption-handling mechanisms used for Parquet files containing OHLCV stock data.

## 1. Directory Structure
All Parquet files are stored locally with the following organizational pattern:

```text
data/parquet/
├── <TICKER>/
│   ├── <TIMEFRAME>.parquet         # Base OHLCV data
│   ├── <TIMEFRAME>_features.parquet # Feature calculated data
│   └── timeframes.json             # Dynamic list of active timeframes for ticker
```
*(e.g., `/data/parquet/AAPL/1D.parquet`)*

## 2. Global Parquet Schema (OHLCV)
All data writes adhere strictly to the following `pyarrow` schema:
```python
import pyarrow as pa
OHLCV_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),  # Unix Epoch Timestamp (Second Precision)
    ("open",      pa.float64()),
    ("high",      pa.float64()),
    ("low",       pa.float64()),
    ("close",     pa.float64()),
    ("volume",    pa.float64()),
])
```

## 3. Atomic File Writes & Reads
To ensure the system never creates corrupt files during container exit or interruption:
- **Target File:** `src/parquet_writer.py`
- **Logic (`append_bars`):**
  1. Reads existing PyArrow table (if present).
  2. Converts newly fetched `OHLCVBar` array to a PyArrow Table matching `OHLCV_SCHEMA`.
  3. Concatenates new table with existing table, deduplicates strictly by `"timestamp"` column.
  4. Writes data to a temporary file (`<path>.tmp`).
  5. Performs an atomic `os.replace()` to swap the `tmp` file seamlessly over the current file.

## 4. Delta Query & Timestamp Resolution
Before firing API queries, the latest point of truth is extracted locally:
- **Logic (`read_last_timestamp`):** Reads the last row's `timestamp` column directly from Parquet. This guarantees only missing historical data is pulled.

## 5. Corruption Handling & Startup Checks
To prevent ingestion of poisoned data on startup or after power failure:
- **Target File:** `src/startup_checks.py`
- **Logic:**
  - Verifies file readability via `pq.read_table(filepath)` for every `.parquet` file.
  - Automatically deletes structurally corrupted Parquet files, extracts the ticker name, and appends it to `<watch_dir>/_recovery.txt` to trigger an automatic re-download.

# Output: Calculated feature data

`/data/parquet/<ticker>/<timeframe>_features.parquet`

- Created by `ParquetStorage.save_ticker_features`.
- Overwrites the entire file.
- Contains identical length and index as the raw data `timeframe.parquet`, with extra columns added.
- Example columns added: `ma_sma_10`, `ma_sma_50`, `ma_ema_20`.
- **Note on Cross-Sectional Features:** Features like `ibd_rs` are initially returned as in-memory pandas Series (`_raw`) over IPC without saving. The main thread aggregates them, ranks them to `1-99`, and then injects the final `ibd_rs` column sequentially via a quick secondary read/write pass across the affected tickers.

## 6. Incremental Coverage Checking
Scans Parquet structure to ensure data is filled:
- **Logic (`check_year_coverage`):** Iterates Parquet rows, buckets by year, and ensures a threshold of >200 bars per year is passed. The current year is routinely skipped and checked.
