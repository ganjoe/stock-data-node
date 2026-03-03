# IMP — F-IMP Requirements Implementation

## Overview

12 new requirements (F-IMP-010 to F-IMP-130, excluding F-IMP-110) are integrated into the existing stock-data-node codebase. Changes are grouped into 10 atomic work orders.

> All work orders modify **existing files**. New modules: `market_clock.py` (T-IMP-007), `run_logger.py` (T-IMP-004), `staleness.py` (T-IMP-008).

---

## Dependency Graph

```
T-IMP-005 (ErrorClassifier)     ─── no dependencies
T-IMP-007 (MarketClock)         ─── no dependencies
T-IMP-008 (Staleness)           ─── no dependencies
T-IMP-004 (RunLogger)           ─── depends on T-IMP-005
T-IMP-001 (BatchConfig/Pacing)  ─── depends on models.py skeleton changes
T-IMP-006 (AutoRegister SKIP)   ─── depends on T-IMP-005
T-IMP-002 (CoverageCheck)       ─── depends on parquet_writer.py
T-IMP-003 (BatchQualify)        ─── depends on gateway_client.py
T-IMP-009 (DescendingOrder)     ─── depends on T-IMP-002
T-IMP-010 (BatchProgress)       ─── depends on T-IMP-004
```

**Implementation Order:** Skeleton → T-IMP-005 → T-IMP-007 → T-IMP-008 → T-IMP-004 → T-IMP-001 → T-IMP-006 → T-IMP-002 → T-IMP-003 → T-IMP-009 → T-IMP-010

---

## PART 1: Skeleton Additions (models.py)

Add to `models.py`:

```python
# ─── New: Adaptive Batch Configuration (F-IMP-010, F-IMP-020) ────

@dataclass(frozen=True)
class BatchConfig:
    """Runtime configuration adapted to detected market data permissions."""
    market_data_type: MarketDataType
    max_concurrent: int       # Live=20, Delayed=1
    base_pacing_delay: float  # Live=0.1, Delayed=3.0, Fallback=5.0
    description: str          # Human-readable label

# ─── New: Error Category Enum (F-IMP-060) ─────────────────────────

class ErrorCategory(str, Enum):
    """Fine-grained IBKR error classification."""
    QUALIFY_FAILED = "QUALIFY_FAILED"
    NO_DATA = "NO_DATA"
    NO_PERMISSIONS = "NO_PERMISSIONS"
    TIMEOUT = "TIMEOUT"
    PACING = "PACING"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

# ─── New: Market Clock Status (F-IMP-080) ─────────────────────────

class MarketStatus(str, Enum):
    """NYSE market status."""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PRE_MARKET = "PRE-MARKET"
    POST_MARKET = "POST-MARKET"
    CLOSED_WEEKEND = "CLOSED (WEEKEND)"
    CLOSED_HOLIDAY = "CLOSED (HOLIDAY)"
```

**Modify `IRateLimiter`** — add method to accept `BatchConfig`:

```python
class IRateLimiter(ABC):
    # ... existing methods ...

    @abstractmethod
    def configure(self, config: BatchConfig) -> None:
        """Applies BatchConfig pacing settings. (F-IMP-020)"""
        ...
```

**Modify `IGatewayClient`** — return `BatchConfig` from detect:

```python
class IGatewayClient(ABC):
    # ... existing methods ...

    @abstractmethod
    async def detect_market_data_type(self) -> BatchConfig:
        """Detects best market data type and returns adaptive config. (F-CON-020, F-IMP-010/020)"""
        ...
```

**Add `IParquetWriter.check_year_coverage`**:

```python
class IParquetWriter(ABC):
    # ... existing methods ...

    @abstractmethod
    def check_year_coverage(self, ticker: str, timeframe: str) -> set[int]:
        """Returns set of years with >200 bars (fully covered). (F-IMP-030)"""
        ...
```

---

## PART 2: Implementation Work Orders

---

### T-IMP-001 — Adaptive Concurrency + Pacing
**Target Files:** `rate_limiter.py`, `gateway_client.py`, `main.py`
**Covers:** F-IMP-010, F-IMP-020

#### Code Stub — rate_limiter.py

```python
class AdaptiveRateLimiter(IRateLimiter):
    # CHANGE: Remove hardcoded BASE_DELAY = 10.0
    # ADD: configurable base delay via configure()

    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._current_delay: float = 10.0   # safe default until configure() is called
        self._base_delay: float = 10.0
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)

    def configure(self, config: BatchConfig) -> None:
        """Applies BatchConfig. (F-IMP-020)"""
        # TODO: Implement
        pass

    async def acquire(self) -> None:
        """Acquires semaphore + waits pacing delay."""
        # TODO: Implement — wrap existing logic with semaphore
        pass
```

#### Logic Steps
1. `configure()`: Set `self._base_delay = config.base_pacing_delay`, `self._current_delay = config.base_pacing_delay`, `self._semaphore = asyncio.Semaphore(config.max_concurrent)`
2. `acquire()`: `async with self._semaphore:` → wait required delay → set `_last_request_time`
3. `report_success()`: Reset `_current_delay` to `_base_delay` (not hardcoded 10.0)

#### Edge Cases
- `configure()` called before any `acquire()` — safe, sets initial values
- `configure()` never called — falls back to 10.0s/1 concurrent (safe delayed default)

#### gateway_client.py Change
- `detect_market_data_type()` returns `BatchConfig` instead of `MarketDataType`:
  - Live detected → `BatchConfig(LIVE, 20, 0.1, "Live (real-time)")`
  - Delayed detected → `BatchConfig(DELAYED, 1, 3.0, "Delayed (15-min)")`
  - Fallback → `BatchConfig(DELAYED, 1, 5.0, "Fallback")`

#### main.py Change
- After `detect_market_data_type()`, call `rate_limiter.configure(batch_config)`
- Log the BatchConfig description

---

### T-IMP-002 — Incremental Coverage Check
**Target File:** `parquet_writer.py`
**Covers:** F-IMP-030

#### Code Stub

```python
class ParquetWriter(IParquetWriter):
    def check_year_coverage(self, ticker: str, timeframe: str) -> set[int]:
        """
        Scans existing parquet to find years with >200 bars.
        Current year is always excluded from "covered".
        """
        # TODO: Implement
        pass
```

#### Logic Steps
1. Read parquet, extract `timestamp` column
2. Convert each timestamp to year via `datetime.fromtimestamp(ts).year`
3. Count bars per year in a `dict[int, int]`
4. Threshold: 200 bars = covered
5. Always remove current year from covered set (force update)
6. Return `set[int]` of covered years

#### Edge Cases
- File missing → return empty set
- Corrupt file → return empty set (log warning)

---

### T-IMP-003 — Batch Contract Qualification
**Target File:** `gateway_client.py`
**Covers:** F-IMP-040

#### Code Stub

```python
class GatewayClient(IGatewayClient):
    async def qualify_contracts_batch(
        self, contracts: dict[str, IBKRContract]
    ) -> dict[str, IBKRContract]:
        """
        Qualifies all contracts in a single IBKR batch call.
        Returns only successfully qualified contracts (conId != 0).
        """
        # TODO: Implement
        pass
```

#### Logic Steps
1. Convert `IBKRContract` dict to `ib_insync.Stock` objects
2. Call `await self._ib.qualifyContractsAsync(*stocks)`
3. Check each result: `conId == 0` → failed, log + skip
4. Return dict of only successful contracts

#### Edge Cases
- Empty dict input → return empty dict
- All fail → return empty dict
- IBKR qualify timeout → log warning, return empty dict

---

### T-IMP-004 — Structured Run Log + Crash-Safe
**Target File:** [NEW] `run_logger.py`
**Covers:** F-IMP-050, F-IMP-120

#### Code Stub

```python
class RunLogger:
    """
    Collects errors and successes during a download run.
    Writes structured JSON log, updated after every batch. (F-IMP-050)
    Crash-safe via try/finally. (F-IMP-120)
    """
    def __init__(self, log_dir: str):
        # TODO: Implement
        pass

    def record_error(self, ticker: str, category: ErrorCategory, message: str, context: str = "") -> None:
        # TODO: Implement
        pass

    def record_success(self, ticker: str, bars_count: int) -> None:
        # TODO: Implement
        pass

    def write(self) -> str:
        """Writes/overwrites log file. Returns path. Called in finally block."""
        # TODO: Implement
        pass

    def print_summary(self) -> None:
        """Prints colorful summary to terminal."""
        # TODO: Implement
        pass
```

#### Logic Steps
1. `__init__`: Store `start_time`, init `errors: dict[ErrorCategory, dict[str, list]]`, `successes: dict[str, int]`, `_failed_symbols: set`
2. `record_error`: Append to `errors[category][ticker]`
3. `record_success`: Increment `successes[ticker] += bars_count`
4. `write`: Build JSON structure with `run_info` (timestamp, duration, totals), `errors_by_category`, `failed_symbols`, `successful_symbols`. Write to `{log_dir}/run_log.json`
5. `print_summary`: Print emoji-annotated per-category error breakdown

#### Edge Cases
- `write()` called with no data → valid JSON with zero counts
- File write failure → log error, don't crash

---

### T-IMP-005 — Fine-Grained Error Classification
**Target File:** `downloader.py`
**Covers:** F-IMP-060

#### Code Stub

```python
def classify_error(error_msg: str) -> ErrorCategory:
    """Classifies an IBKR error message into one of 7 categories."""
    # TODO: Implement
    pass
```

#### Logic Steps
1. Check patterns (case-insensitive):
   - `"qualify"` OR `"no security definition"` → `QUALIFY_FAILED`
   - `"no data"` OR `"hmds"` OR `"keine daten"` OR `"ergab keine"` → `NO_DATA`
   - `"permission"` OR `"not allowed"` OR `"abonnement"` → `NO_PERMISSIONS`
   - `"timeout"` → `TIMEOUT`
   - `"pacing"` OR `"violation"` → `PACING`
   - `"cancelled"` OR `"query cancelled"` → `CANCELLED`
   - else → `UNKNOWN`
2. **Replace** existing `PERMANENT_ERROR_PATTERNS` / `PACING_ERROR_PATTERNS` with `classify_error()` calls
3. `NO_DATA` must **NOT** trigger blacklisting (it's not permanent)

#### Edge Cases
- German error messages ("keine Daten", "Abonnement") must be matched
- Empty error string → `UNKNOWN`

---

### T-IMP-006 — Auto-Register Failed → SKIP (ticker_map.json)
**Target Files:** `ticker_resolver.py`, `config_loader.py`
**Covers:** F-IMP-070

#### Logic Steps
1. In `config_loader.py` → `get_ticker_map()`: When loading `ticker_map.json`, entries with `null` value → return special sentinel (e.g. `IBKRContract(symbol="SKIP", ...)`)
2. In `ticker_resolver.py` → `resolve()`: If resolved contract is SKIP sentinel → return `None` and log `"Ticker %s mapped to null — skipping"`
3. In `downloader.py` → `_process_request()`: When `classify_error()` returns `QUALIFY_FAILED`, write `null` entry to `ticker_map.json` instead of `failed_ticker.json`
4. Add method to `ConfigLoader`: `register_unmapped_ticker(self, ticker: str)` — adds `{ticker: null}` if not already present

#### Edge Cases
- Ticker already has a non-null mapping → never overwrite
- Manual edit: user changes `null` → `"SYMBOL:EUR:FWB2"` → hot-reload picks it up

---

### T-IMP-007 — MarketClock Module
**Target File:** [NEW] `market_clock.py`
**Covers:** F-IMP-080

#### Code Stub

```python
class MarketClock:
    """NYSE market hours awareness. (F-IMP-080)"""
    TIMEZONE = pytz.timezone('US/Eastern')
    OPEN_TIME = time(9, 30)
    CLOSE_TIME = time(16, 0)
    HOLIDAYS: set[date] = { ... }  # 2024-2027

    @staticmethod
    def get_status() -> dict:
        """Returns status, server_time_et, next_event, seconds_to_next_event, calendar_outdated."""
        # TODO: Implement
        pass

    @staticmethod
    def _get_next_open(current_dt: datetime) -> datetime:
        """Finds next valid trading day."""
        # TODO: Implement
        pass
```

#### Logic Steps
1. Hardcode NYSE holidays for 2024-2027
2. `get_status()`: Holiday → CLOSED_HOLIDAY, Weekend → CLOSED_WEEKEND, <9:30 → PRE_MARKET, 9:30-16:00 → OPEN, >16:00 → POST_MARKET
3. Calculate `seconds_to_next_event`
4. Check `calendar_outdated` if `now.year > max_holiday_year`

#### Edge Cases
- Calendar outdated → append warning to status string

---

### T-IMP-008 — Timeframe-Aware Staleness Check
**Target File:** [NEW] `staleness.py`
**Covers:** F-IMP-090

#### Code Stub

```python
def is_stale(last_timestamp: int, timeframe: str) -> bool:
    """Checks if cached data is stale given last bar's unix timestamp."""
    # TODO: Implement
    pass
```

#### Logic Steps
1. Convert `last_timestamp` to datetime
2. By timeframe: `"1D"` → stale if date < today, `"1h"` → > 3600s, `"5m"` → > 300s, etc.
3. Default → `True` (assume stale for safety)

---

### T-IMP-009 — Descending Year Order
**Target File:** `downloader.py`
**Covers:** F-IMP-100

#### Logic Steps
1. In `_calculate_chunks()`: After building `chunks` list ascending, call `chunks.reverse()`
2. First chunk = most recent year → user gets usable data immediately

#### Edge Cases
- Delta download → only 1-2 recent chunks anyway
- Full download → 20 chunks, most recent first

---

### T-IMP-010 — Batch Progress Reporting
**Target File:** `downloader.py`
**Covers:** F-IMP-130

#### Logic Steps
1. In `_process_request()`: Track `chunk_success_count`, `chunk_fail_count`, `total_bars_received`, `start_time`
2. After all chunks: log `📊 Batch: AAPL/1D — 20/20 chunks OK, 5023 bars, 3.2s`
3. Log at INFO level after `request.status = TickerStatus.DONE`

#### Edge Cases
- All chunks fail → report `0/20 chunks OK, 0 bars`
- Preempted → don't print summary (request re-queued)

---

## Verification Plan

### Automated (Syntax Check)
```bash
cd /Users/daniel/stock-data-node
for f in src/*.py; do /opt/anaconda3/bin/python3 -m py_compile "$f"; done && echo "ALL OK"
```

### Integration (Validation Script)
```bash
/opt/anaconda3/bin/python3 tests/validate_pipeline.py
```
**Expected output changes:**
1. BatchConfig displayed: `"Delayed (15-min), concurrent=1, pacing=3.0s"`
2. Chunk order: most recent year first (2026 → 2006)
3. Per-ticker batch summary line
4. JSON run log written to `_validation_tmp/state/run_log.json`
5. First data printed should be from 2025/2026 (not 2006)
