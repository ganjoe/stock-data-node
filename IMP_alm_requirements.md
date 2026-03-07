# IMP_alm_requirements — Implementation Specification

> **Source:** `alm_requirements` (24 Requirements)
> **Author:** Senior Software Architect (AI-Optimized Workflow)
> **Target:** Junior AI Agents (atomic, isolated tasks)
> **Language:** Python 3.11+ | **Libraries:** `ib_insync`, `pyarrow`, `fastapi`, `uvicorn`, `watchdog`

---

## PART 1: The System Skeleton (Shared Context)

> This code is **read-only context** for all Junior AI Agents. It defines all data structures, enums, interfaces, and configuration schemas. No Junior AI may modify these definitions.

### 1.1 Project Structure

```
stock-data-node/
├── config/
│   ├── gateway.json
│   ├── paths.json
│   └── ticker_map.json
├── data/parquet/<TICKER>/
│   ├── timeframes.json          # optional, default: ["1D"]
│   ├── <TIMEFRAME>.parquet
│   ├── indikator.json           # (extern, nicht Scope)
│   └── indikator.parquet        # (extern, nicht Scope)
├── watch/                       # *.txt Ticker-Listen
├── logs/error.log
├── state/failed_ticker.json
├── src/
│   ├── __init__.py
│   ├── main.py                  # Entrypoint
│   ├── models.py                # ← THIS FILE (Skeleton)
│   ├── config_loader.py         # T-001
│   ├── gateway_client.py        # T-002
│   ├── ticker_resolver.py       # T-003
│   ├── priority_queue.py        # T-004
│   ├── downloader.py            # T-005
│   ├── parquet_writer.py        # T-006
│   ├── file_watcher.py          # T-007
│   ├── api_server.py            # T-008
│   ├── rate_limiter.py          # T-009
│   ├── failed_ticker_store.py   # T-010
│   ├── startup_checks.py        # T-011
│   └── main.py                  # T-012
├── Dockerfile                   # T-013
├── docker-compose.yml           # T-013
└── requirements.txt             # T-013
```

### 1.2 Data Structures (`src/models.py`)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Optional
import time


# ─── Enums ────────────────────────────────────────────────────────

class MarketDataType(IntEnum):
    """IBKR Market Data Types."""
    LIVE = 1
    FROZEN = 2
    DELAYED = 3
    DELAYED_FROZEN = 4


class DownloadPriority(IntEnum):
    """Queue priority levels. Lower value = higher priority."""
    API = 1       # REST-API requests (highest)
    WATCHER = 2   # File-watcher requests


class TickerStatus(str, Enum):
    """Processing status of a ticker in the queue."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"   # in failed_ticker.json blacklist


class TimeframePhase(str, Enum):
    """Bulk download phase ordering."""
    DAILY = "daily"       # Phase 1: all tickers 1D
    SECONDARY = "secondary"  # Phase 2: remaining timeframes


# ─── Config Data Structures ──────────────────────────────────────

@dataclass(frozen=True)
class GatewayEndpoint:
    """Single gateway connection endpoint."""
    host: str
    port: int


@dataclass(frozen=True)
class GatewayConfig:
    """Loaded from config/gateway.json. (F-CFG-010)"""
    live: GatewayEndpoint
    paper: GatewayEndpoint
    mode: str   # "live" | "paper"

    @property
    def active_endpoint(self) -> GatewayEndpoint:
        return self.live if self.mode == "live" else self.paper


@dataclass(frozen=True)
class PathsConfig:
    """Loaded from config/paths.json. (F-CFG-020)"""
    parquet_dir: str     # e.g. "/app/data/parquet"
    watch_dir: str       # e.g. "/app/watch"


@dataclass
class IBKRContract:
    """Single entry from config/ticker_map.json. (F-FNC-040)"""
    symbol: str
    exchange: str
    currency: str
    sec_type: str        # "STK", "FUT", etc.


# ─── Domain Objects ──────────────────────────────────────────────

@dataclass
class DownloadRequest:
    """A single unit of work in the priority queue."""
    ticker: str
    timeframe: str
    priority: DownloadPriority
    contract: Optional[IBKRContract] = None
    status: TickerStatus = TickerStatus.PENDING
    created_at: float = field(default_factory=time.time)

    def __lt__(self, other: DownloadRequest) -> bool:
        """For heapq ordering: lower priority value = higher priority."""
        return (self.priority, self.created_at) < (other.priority, other.created_at)


@dataclass
class DownloadChunk:
    """A time-bounded chunk of a historical download. (F-FNC-020)"""
    ticker: str
    timeframe: str
    start_ts: int        # Unix timestamp
    end_ts: int          # Unix timestamp
    is_final: bool       # True if this is the last chunk


@dataclass
class OHLCVBar:
    """A single OHLCV bar as returned by IBKR."""
    timestamp: int       # Unix timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class DownloadResult:
    """Result of downloading bars for one chunk."""
    ticker: str
    timeframe: str
    bars: list[OHLCVBar]
    is_delta: bool       # True if this was a delta download
    error: Optional[str] = None


@dataclass
class FailedTickerEntry:
    """Single entry in state/failed_ticker.json. (F-FNC-070)"""
    ticker: str
    reason: str
    timestamp: str       # ISO 8601
    source: str          # "watch_file" | "api"
```

### 1.3 Interfaces (Abstract Base Classes)

```python
from abc import ABC, abstractmethod
from typing import Optional


class IConfigLoader(ABC):
    """Loads and hot-reloads configuration. (F-CFG-010/020/030/040)"""

    @abstractmethod
    def get_gateway_config(self) -> GatewayConfig: ...

    @abstractmethod
    def get_paths_config(self) -> PathsConfig: ...

    @abstractmethod
    def get_ticker_map(self) -> dict[str, IBKRContract]: ...

    @abstractmethod
    def get_timeframes_for_ticker(self, ticker: str) -> list[str]:
        """Returns timeframes from <ticker>/timeframes.json or default ["1D"]."""
        ...

    @abstractmethod
    def add_timeframe_to_ticker(self, ticker: str, timeframe: str) -> None:
        """Appends a timeframe to <ticker>/timeframes.json. (F-CFG-050)"""
        ...

    @abstractmethod
    def reload_if_changed(self) -> None:
        """Checks file mtimes and reloads changed configs. (F-CFG-040)"""
        ...


class IGatewayClient(ABC):
    """Manages IBKR Gateway connection. (F-CON-010/020)"""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    async def detect_market_data_type(self) -> MarketDataType:
        """Detects best available market data type. (F-CON-020)"""
        ...

    @abstractmethod
    async def request_historical_bars(
        self, contract: IBKRContract, timeframe: str,
        start_ts: int, end_ts: int
    ) -> list[OHLCVBar]:
        """Downloads historical bars for a single chunk."""
        ...


class IPriorityQueue(ABC):
    """Thread-safe priority queue with preemption. (F-FNC-010/020)"""

    @abstractmethod
    def enqueue(self, request: DownloadRequest) -> None: ...

    @abstractmethod
    def dequeue(self) -> Optional[DownloadRequest]: ...

    @abstractmethod
    def has_higher_priority_waiting(self, current_priority: DownloadPriority) -> bool:
        """Checks if a higher-prio item is waiting (for chunk preemption)."""
        ...

    @abstractmethod
    def size(self) -> int: ...


class ITickerResolver(ABC):
    """Resolves ticker symbols to IBKR contracts. (F-FNC-040)"""

    @abstractmethod
    def resolve(self, ticker: str) -> Optional[IBKRContract]:
        """Returns None if ticker not in ticker_map.json."""
        ...


class IParquetWriter(ABC):
    """Atomic parquet write operations. (F-DAT-010/020)"""

    @abstractmethod
    def read_last_timestamp(self, ticker: str, timeframe: str) -> Optional[int]:
        """Returns last timestamp in existing parquet, or None. (F-FNC-030)"""
        ...

    @abstractmethod
    def append_bars(self, ticker: str, timeframe: str, bars: list[OHLCVBar]) -> None:
        """Atomic append via temp-file + rename. (F-DAT-020)"""
        ...

    @abstractmethod
    def validate_parquet(self, filepath: str) -> bool:
        """Returns True if parquet file is readable. (F-SYS-020)"""
        ...


class IRateLimiter(ABC):
    """Adaptive rate limiting with backoff. (F-FNC-050)"""

    @abstractmethod
    async def acquire(self) -> None:
        """Blocks until a request slot is available."""
        ...

    @abstractmethod
    def report_pacing_error(self) -> None:
        """Triggers exponential backoff."""
        ...

    @abstractmethod
    def report_success(self) -> None:
        """Resets backoff on successful request."""
        ...


class IFailedTickerStore(ABC):
    """Manages failed_ticker.json blacklist. (F-FNC-070)"""

    @abstractmethod
    def is_blacklisted(self, ticker: str) -> bool: ...

    @abstractmethod
    def add(self, entry: FailedTickerEntry) -> None: ...

    @abstractmethod
    def load(self) -> list[FailedTickerEntry]: ...
```

---

## PART 2: Implementation Work Orders

---

### T-001 — Config Loader

| Field | Value |
|-------|-------|
| **Target File** | `src/config_loader.py` |
| **Description** | Load all JSON configs, provide per-ticker timeframes, and support hot-reload |
| **Covers** | F-CFG-010, F-CFG-020, F-CFG-030, F-CFG-040, F-CFG-050 |
| **Context** | Implements `IConfigLoader`. Uses `GatewayConfig`, `PathsConfig`, `IBKRContract` |

**Code Stub:**

```python
import json
import os
from pathlib import Path
from typing import Optional
from models import (
    IConfigLoader, GatewayConfig, GatewayEndpoint,
    PathsConfig, IBKRContract
)


class ConfigLoader(IConfigLoader):
    """
    Loads all JSON configuration files and monitors them for changes.
    """
    DEFAULT_TIMEFRAMES = ["1D"]

    def __init__(self, config_dir: str, parquet_dir: str):
        self._config_dir = Path(config_dir)
        self._parquet_dir = Path(parquet_dir)
        self._gateway_config: Optional[GatewayConfig] = None
        self._paths_config: Optional[PathsConfig] = None
        self._ticker_map: dict[str, IBKRContract] = {}
        self._file_mtimes: dict[str, float] = {}
        # TODO: Implement

    def get_gateway_config(self) -> GatewayConfig:
        # TODO: Implement
        ...

    def get_paths_config(self) -> PathsConfig:
        # TODO: Implement
        ...

    def get_ticker_map(self) -> dict[str, IBKRContract]:
        # TODO: Implement
        ...

    def get_timeframes_for_ticker(self, ticker: str) -> list[str]:
        # TODO: Implement
        ...

    def add_timeframe_to_ticker(self, ticker: str, timeframe: str) -> None:
        # TODO: Implement
        ...

    def reload_if_changed(self) -> None:
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`__init__`**: Call `_load_all()` to load all configs on startup.
2. **`get_gateway_config`**: Parse `config/gateway.json` → create `GatewayEndpoint` for `live` and `paper` → return `GatewayConfig`. Cache result.
3. **`get_paths_config`**: Parse `config/paths.json` → return `PathsConfig`. Cache result.
4. **`get_ticker_map`**: Parse `config/ticker_map.json` → for each key, create `IBKRContract` → return dict. Cache result.
5. **`get_timeframes_for_ticker(ticker)`**: Check if `<parquet_dir>/<ticker>/timeframes.json` exists. If yes, read and return list. If no, return `DEFAULT_TIMEFRAMES` (`["1D"]`).
6. **`add_timeframe_to_ticker(ticker, timeframe)`**: Read current timeframes (or default). If `timeframe` not in list, append it. Write updated list to `<parquet_dir>/<ticker>/timeframes.json`. Create directory if needed.
7. **`reload_if_changed`**: For each tracked config file, compare `os.path.getmtime()` with stored mtime. If changed, reload that specific config. Log reloaded file names.

**Edge Cases:**
- If `gateway.json` is missing → raise `FileNotFoundError` with clear message
- If `ticker_map.json` is missing → raise `FileNotFoundError` with clear message
- If `paths.json` is missing → raise `FileNotFoundError` with clear message
- If `timeframes.json` contains invalid JSON → log error, return `DEFAULT_TIMEFRAMES`

---

### T-002 — Gateway Client

| Field | Value |
|-------|-------|
| **Target File** | `src/gateway_client.py` |
| **Description** | Connect to IBKR Gateway via `ib_insync`, detect market data type |
| **Covers** | F-CON-010, F-CON-020, F-SYS-030 |
| **Context** | Implements `IGatewayClient`. Uses `GatewayConfig`, `IBKRContract`, `OHLCVBar`, `MarketDataType` |

**Code Stub:**

```python
from ib_insync import IB, Contract, util
from models import (
    IGatewayClient, GatewayConfig, IBKRContract,
    OHLCVBar, MarketDataType
)


class GatewayClient(IGatewayClient):
    """
    Manages the ib_insync connection to an IB Gateway instance.
    """

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._ib = IB()
        self._market_data_type: MarketDataType = MarketDataType.DELAYED
        # TODO: Implement

    async def connect(self) -> None:
        # TODO: Implement
        ...

    async def disconnect(self) -> None:
        # TODO: Implement
        ...

    def is_connected(self) -> bool:
        # TODO: Implement
        ...

    async def detect_market_data_type(self) -> MarketDataType:
        # TODO: Implement
        ...

    async def request_historical_bars(
        self, contract: IBKRContract, timeframe: str,
        start_ts: int, end_ts: int
    ) -> list[OHLCVBar]:
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`connect`**: Get `active_endpoint` from config. Call `self._ib.connectAsync(host, port, clientId=1)`. If connection fails → log error to terminal, raise `ConnectionError`.
2. **`detect_market_data_type`**: Call `self._ib.reqMarketDataType(MarketDataType.LIVE)`. Request a small test (e.g., one bar of SPY). If data comes back as live → return `LIVE`. If delayed → return `DELAYED`. Log result to terminal.
3. **`request_historical_bars`**: Convert `IBKRContract` to `ib_insync.Contract`. Map `timeframe` string to IBKR's `barSizeSetting` and `durationStr`. Call `self._ib.reqHistoricalDataAsync(...)`. Convert result list to `list[OHLCVBar]` with Unix timestamps.
4. **`disconnect`**: Call `self._ib.disconnect()`.
5. **`is_connected`**: Return `self._ib.isConnected()`.

**Edge Cases:**
- Connection timeout → log error, raise `ConnectionError`
- `reqHistoricalData` returns empty list → return empty `list[OHLCVBar]` (no error)
- Pacing violation error from IBKR → let it propagate (handled by caller + rate limiter)

**IBKR Timeframe Mapping Table (provide as constant):**

```python
TIMEFRAME_MAP = {
    "1m":  ("1 min",  "1 D"),
    "5m":  ("5 mins", "1 W"),
    "15m": ("15 mins","2 W"),
    "1h":  ("1 hour", "1 M"),
    "1D":  ("1 day",  "1 Y"),
    "1W":  ("1 week", "5 Y"),
    "1M":  ("1 month","10 Y"),
}
```

---

### T-003 — Ticker Resolver

| Field | Value |
|-------|-------|
| **Target File** | `src/ticker_resolver.py` |
| **Description** | Resolve ticker symbols to IBKR contracts, check blacklist |
| **Covers** | F-FNC-040, F-FNC-070 (read) |
| **Context** | Implements `ITickerResolver`. Uses `IConfigLoader`, `IFailedTickerStore`, `IBKRContract` |

**Code Stub:**

```python
from typing import Optional
from models import ITickerResolver, IBKRContract, IConfigLoader, IFailedTickerStore


class TickerResolver(ITickerResolver):
    """
    Resolves ticker strings to IBKRContract objects.
    Checks blacklist before resolving.
    """

    def __init__(self, config: IConfigLoader, failed_store: IFailedTickerStore):
        self._config = config
        self._failed_store = failed_store
        # TODO: Implement

    def resolve(self, ticker: str) -> Optional[IBKRContract]:
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`resolve(ticker)`**:
   - Normalize ticker string: `ticker.strip().upper()`
   - Check `self._failed_store.is_blacklisted(ticker)` → if True, log "Skipping blacklisted ticker" and return `None`
   - Call `self._config.reload_if_changed()` (hot-reload ticker_map)
   - Lookup ticker in `self._config.get_ticker_map()`
   - If found → return `IBKRContract`
   - If not found → return `None` (caller decides whether to add to failed list)

**Edge Cases:**
- Ticker with whitespace → strip and uppercase
- Empty string → return `None`

---

### T-004 — Priority Queue

| Field | Value |
|-------|-------|
| **Target File** | `src/priority_queue.py` |
| **Description** | Thread-safe priority queue with preemption check |
| **Covers** | F-FNC-010, F-FNC-020 |
| **Context** | Implements `IPriorityQueue`. Uses `DownloadRequest`, `DownloadPriority` |

**Code Stub:**

```python
import heapq
import threading
from typing import Optional
from models import IPriorityQueue, DownloadRequest, DownloadPriority


class DownloadQueue(IPriorityQueue):
    """
    Thread-safe min-heap priority queue.
    DownloadRequest.__lt__ ensures API (prio 1) < WATCHER (prio 2).
    """

    def __init__(self):
        self._heap: list[DownloadRequest] = []
        self._lock = threading.Lock()
        # TODO: Implement

    def enqueue(self, request: DownloadRequest) -> None:
        # TODO: Implement
        ...

    def dequeue(self) -> Optional[DownloadRequest]:
        # TODO: Implement
        ...

    def has_higher_priority_waiting(self, current_priority: DownloadPriority) -> bool:
        # TODO: Implement
        ...

    def size(self) -> int:
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`enqueue(request)`**: Acquire lock. `heapq.heappush(self._heap, request)`. Log queue size.
2. **`dequeue()`**: Acquire lock. If heap is empty → return `None`. Else `heapq.heappop(self._heap)` and return it.
3. **`has_higher_priority_waiting(current_priority)`**: Acquire lock. Peek at `self._heap[0].priority` (if heap not empty). Return `True` if peek priority < current_priority. This is used during chunk downloads to check if preemption is needed.
4. **`size()`**: Return `len(self._heap)`.

**Edge Cases:**
- `dequeue()` on empty queue → return `None` (do NOT block)
- `has_higher_priority_waiting()` on empty queue → return `False`

---

### T-005 — Downloader (Core Orchestrator)

| Field | Value |
|-------|-------|
| **Target File** | `src/downloader.py` |
| **Description** | Core download loop: chunking, delta detection, preemption, phase ordering |
| **Covers** | F-FNC-020, F-FNC-030, F-FNC-060 |
| **Context** | Uses `IGatewayClient`, `IPriorityQueue`, `IParquetWriter`, `IRateLimiter`, `IConfigLoader`, `IFailedTickerStore`, `DownloadRequest`, `DownloadChunk`, `OHLCVBar`, `DownloadResult` |

**Code Stub:**

```python
import logging
from models import (
    IGatewayClient, IPriorityQueue, IParquetWriter,
    IRateLimiter, IConfigLoader, IFailedTickerStore,
    DownloadRequest, DownloadChunk, DownloadResult,
    OHLCVBar, TickerStatus, DownloadPriority, FailedTickerEntry,
    TimeframePhase
)

logger = logging.getLogger(__name__)


class Downloader:
    """
    Core download orchestrator.
    Processes the priority queue, downloads in chunks,
    supports preemption and delta downloads.
    """

    CHUNK_DURATION_SECONDS = 86400 * 30  # 30 days per chunk

    def __init__(
        self,
        gateway: IGatewayClient,
        queue: IPriorityQueue,
        writer: IParquetWriter,
        rate_limiter: IRateLimiter,
        config: IConfigLoader,
        failed_store: IFailedTickerStore,
    ):
        self._gateway = gateway
        self._queue = queue
        self._writer = writer
        self._rate_limiter = rate_limiter
        self._config = config
        self._failed_store = failed_store
        self._running = True
        # TODO: Implement

    async def run_loop(self) -> None:
        """Main processing loop. Runs until stopped."""
        # TODO: Implement
        ...

    async def _process_request(self, request: DownloadRequest) -> None:
        """Processes a single download request with chunking."""
        # TODO: Implement
        ...

    def _calculate_chunks(
        self, last_ts: int | None, timeframe: str
    ) -> list[DownloadChunk]:
        """Splits a download into time-bounded chunks."""
        # TODO: Implement
        ...

    async def _download_chunk(self, chunk: DownloadChunk, contract) -> DownloadResult:
        """Downloads a single chunk via gateway."""
        # TODO: Implement
        ...

    def stop(self) -> None:
        self._running = False
```

**Algo/Logic Steps:**

1. **`run_loop`**:
   - Loop while `self._running`:
     - Call `self._config.reload_if_changed()`
     - Dequeue next request from `self._queue`
     - If `None` → sleep 1 second, continue
     - Log: "Processing {ticker} / {timeframe}"
     - Call `self._process_request(request)`
   - On shutdown: log "Downloader stopped"

2. **`_process_request(request)`**:
   - Lookup last timestamp via `self._writer.read_last_timestamp(ticker, timeframe)` **(F-FNC-030 Delta)**
   - Calculate chunks via `self._calculate_chunks(last_ts, timeframe)`
   - For each chunk:
     - **Check preemption (F-FNC-020):** Call `self._queue.has_higher_priority_waiting(request.priority)`. If True AND request.priority is `WATCHER`: re-enqueue current request, break, and process the higher-priority item first.
     - Call `self._rate_limiter.acquire()` **(F-FNC-050)**
     - Call `self._download_chunk(chunk, request.contract)`
     - If result has error:
       - If pacing error → `self._rate_limiter.report_pacing_error()`, retry chunk
       - If permanent error → add to `failed_store`, set status to `FAILED`, return
     - Else → `self._rate_limiter.report_success()`, call `self._writer.append_bars()`
   - Set status to `DONE`, log completion

3. **`_calculate_chunks(last_ts, timeframe)`**:
   - If `last_ts` is not None → `start = last_ts + 1` (delta)
   - Else → `start = now - MAX_HISTORY_LOOKBACK` (based on timeframe)
   - `end = now`
   - Split `[start, end]` into `CHUNK_DURATION_SECONDS` intervals
   - Return list of `DownloadChunk` objects, mark last one as `is_final=True`

4. **`_download_chunk`**: Call `self._gateway.request_historical_bars(...)`. Wrap result in `DownloadResult`.

**Edge Cases:**
- Gateway disconnected mid-download → catch error, log, re-enqueue request
- Empty bars returned → not an error, just skip append (no new data)
- Pacing error → exponential backoff via rate limiter, retry same chunk

---

### T-006 — Parquet Writer

| Field | Value |
|-------|-------|
| **Target File** | `src/parquet_writer.py` |
| **Description** | Atomic parquet read/write with corruption validation |
| **Covers** | F-DAT-010, F-DAT-020, F-SYS-020 |
| **Context** | Implements `IParquetWriter`. Uses `OHLCVBar` |

**Code Stub:**

```python
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from typing import Optional
from models import IParquetWriter, OHLCVBar

# Schema definition for all OHLCV parquet files
OHLCV_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),
    ("open",      pa.float64()),
    ("high",      pa.float64()),
    ("low",       pa.float64()),
    ("close",     pa.float64()),
    ("volume",    pa.float64()),
])


class ParquetWriter(IParquetWriter):
    """
    Handles all parquet I/O with atomic writes.
    """

    def __init__(self, parquet_dir: str):
        self._parquet_dir = Path(parquet_dir)
        # TODO: Implement

    def _get_parquet_path(self, ticker: str, timeframe: str) -> Path:
        return self._parquet_dir / ticker / f"{timeframe}.parquet"

    def read_last_timestamp(self, ticker: str, timeframe: str) -> Optional[int]:
        # TODO: Implement
        ...

    def append_bars(self, ticker: str, timeframe: str, bars: list[OHLCVBar]) -> None:
        # TODO: Implement
        ...

    def validate_parquet(self, filepath: str) -> bool:
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`read_last_timestamp`**: If parquet file exists → read with `pq.read_table()` → get last row's `timestamp` column value → return it. If file doesn't exist → return `None`.
2. **`append_bars(ticker, timeframe, bars)`**:
   - If `bars` is empty → return immediately
   - Convert `list[OHLCVBar]` to `pa.Table` using `OHLCV_SCHEMA`
   - If existing parquet exists → read it, concatenate with new table, deduplicate by timestamp
   - Write to **temp file** `<path>.tmp`
   - **Atomic rename** `<path>.tmp` → `<path>` via `os.replace()` **(F-DAT-020)**
   - Create ticker directory if it doesn't exist
3. **`validate_parquet(filepath)`**: Try `pq.read_table(filepath)`. If successful → return `True`. If any exception → return `False`.

**Edge Cases:**
- Directory doesn't exist → `os.makedirs(exist_ok=True)`
- `os.replace()` is atomic on POSIX (which Docker uses) — no half-written files
- Deduplication: sort by timestamp, drop duplicates

---

### T-007 — File Watcher

| Field | Value |
|-------|-------|
| **Target File** | `src/file_watcher.py` |
| **Description** | Watch directory for .txt files, parse tickers, enqueue, cleanup |
| **Covers** | F-INT-020, F-INT-030 |
| **Context** | Uses `IPriorityQueue`, `ITickerResolver`, `IConfigLoader`, `DownloadRequest`, `DownloadPriority` |

**Code Stub:**

```python
import os
import re
import logging
from pathlib import Path
from models import (
    IPriorityQueue, ITickerResolver, IConfigLoader,
    DownloadRequest, DownloadPriority, FailedTickerEntry,
    IFailedTickerStore
)

logger = logging.getLogger(__name__)


class FileWatcher:
    """
    Monitors watch directory for .txt files containing ticker symbols.
    """

    def __init__(
        self,
        watch_dir: str,
        queue: IPriorityQueue,
        resolver: ITickerResolver,
        config: IConfigLoader,
        failed_store: IFailedTickerStore,
    ):
        self._watch_dir = Path(watch_dir)
        self._queue = queue
        self._resolver = resolver
        self._config = config
        self._failed_store = failed_store
        # TODO: Implement

    def scan_once(self) -> None:
        """Scans watch dir, parses all .txt files, enqueues tickers."""
        # TODO: Implement
        ...

    def _parse_ticker_file(self, filepath: Path) -> list[str]:
        """Parses a single watch file into a list of ticker strings."""
        # TODO: Implement
        ...

    def _cleanup_file(self, filepath: Path) -> None:
        """Removes processed tickers or deletes empty file."""
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`scan_once`**: List all `*.txt` files in `watch_dir`. For each file: call `_parse_ticker_file()`. For each ticker:
   - Resolve via `self._resolver.resolve(ticker)`
   - If `None` (not in map) → add to `failed_store`, log warning, remove ticker from file
   - If blacklisted → log skip, remove ticker from file
   - Else → create `DownloadRequest` with `priority=WATCHER` and all timeframes from `self._config.get_timeframes_for_ticker(ticker)`. **Daily timeframe first.** Enqueue.
   - After processing all tickers in file → call `_cleanup_file()`
2. **`_parse_ticker_file(filepath)`**: Read file content. Split by **commas, whitespace, or newlines** (regex: `r'[,\s]+'`). Strip each token. Filter out empty strings. Return list.
3. **`_cleanup_file(filepath)`**: If all tickers in file were processed → delete the file **(F-INT-030)**. If some tickers remain (e.g., failed) → rewrite the file with remaining tickers.

**Edge Cases:**
- File is empty → delete it immediately
- File has mixed separators (`AAPL, MSFT\nGOOG SAP`) → regex handles all
- File disappears between listing and reading → catch `FileNotFoundError`, skip

---

### T-008 — REST API Server

| Field | Value |
|-------|-------|
| **Target File** | `src/api_server.py` |
| **Description** | Minimal FastAPI server for ticker download requests and global staleness trigger |
| **Covers** | F-INT-010, F-CFG-050, F-API-010, F-API-020, F-API-030, F-API-040 |
| **Context** | Uses `IPriorityQueue`, `ITickerResolver`, `IConfigLoader`, `DownloadRequest`, `DownloadPriority` |

**Code Stub:**

```python
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from models import (
    IPriorityQueue, ITickerResolver, IConfigLoader,
    DownloadRequest, DownloadPriority, IFailedTickerStore,
    FailedTickerEntry
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Stock Data Node API")


class TickerRequest(BaseModel):
    ticker: str
    timeframes: list[str] | None = None  # None = use configured/default


class TickerResponse(BaseModel):
    ticker: str
    timeframes: list[str]
    status: str  # "queued" | "error"
    message: str


def create_api(
    queue: IPriorityQueue,
    resolver: ITickerResolver,
    config: IConfigLoader,
    failed_store: IFailedTickerStore,
    watcher: "FileWatcher",
) -> FastAPI:
    # TODO: Implement POST /download endpoint
    # TODO: Implement POST /trigger-staleness endpoint
    # TODO: Implement GET /status endpoint (optional)
    ...
```

1. **`POST /download`**: Accept `TickerRequest` body.
   - Resolve ticker via `resolver.resolve()`
   - If unresolved → return error response, add to `failed_store`
   - If `timeframes` provided → persist each new timeframe via `config.add_timeframe_to_ticker()` **(F-CFG-050)**
   - If `timeframes` not provided → use `config.get_timeframes_for_ticker()`
   - Create `DownloadRequest` for each timeframe with `priority=API`
   - Enqueue all requests
   - Return `TickerResponse(status="queued", ...)`
2. **`POST /trigger-staleness`**: No body required. **(F-API-010)**
   - Call `watcher.scan_once()` to process any pending files in the watch directory first.
   - Scan the `config.get_paths_config().parquet_dir` for all subdirectories (each represents a valid ticker). **(F-API-020)**
   - For each found ticker directory:
     - Fetch timeframes via `config.get_timeframes_for_ticker(ticker)`
     - For each timeframe, create a `DownloadRequest` with `priority=WATCHER` (System prio 2). **(F-API-030)**
     - Enqueue the request.
   - Return a synchronous `202 Accepted` response immediately with JSON payload: `{"status": "accepted", "tickers_evaluated": <count>}`. **(F-API-040)**
3. **`GET /status`** (optional, minimal): Return queue size and current processing ticker. Keep it simple.

**Edge Cases:**
- Empty ticker string → return 400
- Unknown ticker → return 404 with error reason

---

### T-009 — Rate Limiter

| Field | Value |
|-------|-------|
| **Target File** | `src/rate_limiter.py` |
| **Description** | Adaptive rate limiting with exponential backoff |
| **Covers** | F-FNC-050 |
| **Context** | Implements `IRateLimiter` |

**Code Stub:**

```python
import asyncio
import time
import logging
from models import IRateLimiter

logger = logging.getLogger(__name__)


class AdaptiveRateLimiter(IRateLimiter):
    """
    Rate limiter respecting IBKR pacing rules:
    - Max 60 requests per 10 minutes
    - Exponential backoff on pacing violations
    """

    BASE_DELAY = 10.0        # seconds between requests (safe default)
    MAX_BACKOFF = 300.0       # 5 min max backoff
    BACKOFF_FACTOR = 2.0

    def __init__(self):
        self._last_request_time: float = 0.0
        self._current_backoff: float = self.BASE_DELAY
        # TODO: Implement

    async def acquire(self) -> None:
        # TODO: Implement
        ...

    def report_pacing_error(self) -> None:
        # TODO: Implement
        ...

    def report_success(self) -> None:
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`acquire`**: Calculate `elapsed = time.time() - self._last_request_time`. If `elapsed < self._current_backoff` → `asyncio.sleep(self._current_backoff - elapsed)`. Update `self._last_request_time = time.time()`. Log wait time if > 0.
2. **`report_pacing_error`**: `self._current_backoff = min(self._current_backoff * BACKOFF_FACTOR, MAX_BACKOFF)`. Log new backoff value.
3. **`report_success`**: `self._current_backoff = self.BASE_DELAY` (reset to normal). 

**Edge Cases:**
- Multiple pacing errors in a row → backoff keeps doubling until MAX_BACKOFF
- First request ever → no wait needed

---

### T-010 — Failed Ticker Store

| Field | Value |
|-------|-------|
| **Target File** | `src/failed_ticker_store.py` |
| **Description** | Read/write `failed_ticker.json` blacklist |
| **Covers** | F-FNC-070 |
| **Context** | Implements `IFailedTickerStore`. Uses `FailedTickerEntry` |

**Code Stub:**

```python
import json
from pathlib import Path
from datetime import datetime, timezone
from models import IFailedTickerStore, FailedTickerEntry


class FailedTickerStore(IFailedTickerStore):
    """
    Manages the failed_ticker.json file.
    File is also manually editable by the user.
    """

    def __init__(self, filepath: str):
        self._filepath = Path(filepath)
        self._cache: dict[str, FailedTickerEntry] = {}
        # TODO: Implement

    def is_blacklisted(self, ticker: str) -> bool:
        # TODO: Implement
        ...

    def add(self, entry: FailedTickerEntry) -> None:
        # TODO: Implement
        ...

    def load(self) -> list[FailedTickerEntry]:
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`__init__`**: Call `self.load()` to populate `self._cache`.
2. **`is_blacklisted(ticker)`**: **Re-read file from disk** (user may have manually edited it). Check if `ticker.upper()` exists in loaded entries.
3. **`add(entry)`**: Load current file. Append entry. Write back to disk as JSON. Update cache.
4. **`load()`**: If file doesn't exist → return empty list. Else → read JSON, parse each entry to `FailedTickerEntry`, return list.

**Edge Cases:**
- File doesn't exist → no blacklisted tickers
- File contains invalid JSON → log error, treat as empty (DON'T crash)
- User deletes an entry manually → `is_blacklisted` re-reads file, so ticker is no longer blacklisted

---

### T-011 — Startup Checks

| Field | Value |
|-------|-------|
| **Target File** | `src/startup_checks.py` |
| **Description** | Parquet corruption check and auto-recovery on startup |
| **Covers** | F-SYS-020, F-SYS-030 |
| **Context** | Uses `IParquetWriter`, `PathsConfig` |

**Code Stub:**

```python
import os
import logging
from pathlib import Path
from models import IParquetWriter, PathsConfig

logger = logging.getLogger(__name__)


class StartupChecker:
    """
    Runs integrity checks at container startup.
    """

    def __init__(self, paths: PathsConfig, writer: IParquetWriter):
        self._paths = paths
        self._writer = writer
        # TODO: Implement

    def run_all_checks(self) -> list[str]:
        """
        Runs all checks. Returns list of tickers that need re-download.
        """
        # TODO: Implement
        ...

    def _check_parquet_integrity(self) -> list[str]:
        """
        Scans all parquet files, validates each.
        Deletes corrupt files and returns affected tickers.
        """
        # TODO: Implement
        ...

    def _write_recovery_watch_file(self, tickers: list[str]) -> None:
        """
        Writes corrupted tickers to a watch file for re-download.
        """
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`run_all_checks`**: Call `_check_parquet_integrity()`. If any corrupt tickers found → call `_write_recovery_watch_file(tickers)`. Log summary to terminal.
2. **`_check_parquet_integrity`**: Walk `parquet_dir`. For each `*.parquet` file → call `self._writer.validate_parquet(filepath)`. If invalid:
   - Log: "CORRUPT: {filepath} — deleting"
   - Delete the file
   - Extract ticker name from path
   - Add to corrupted tickers list
   - Return deduplicated list
3. **`_write_recovery_watch_file(tickers)`**: Write tickers (one per line) to `<watch_dir>/_recovery.txt`. Log: "Recovery file written with {N} tickers."

**Edge Cases:**
- `parquet_dir` is empty → no checks needed, return empty list
- All files are valid → log "All parquet files OK", return empty list

---

### T-012 — Main Entrypoint (Orchestration)

| Field | Value |
|-------|-------|
| **Target File** | `src/main.py` |
| **Description** | Wire all components together, configure logging, run event loop |
| **Covers** | F-LOG-010, F-LOG-020, F-SYS-010 |
| **Context** | Instantiates ALL components, dependency injection |

**Code Stub:**

```python
import asyncio
import logging
import signal
import sys


async def main():
    """
    Application entrypoint:
    1. Configure logging (file + terminal)
    2. Load configs
    3. Run startup checks
    4. Connect to gateway
    5. Start file watcher (background)
    6. Start API server (background)
    7. Start download loop
    """
    # TODO: Implement
    ...


if __name__ == "__main__":
    asyncio.run(main())
```

**Algo/Logic Steps:**

1. **Configure logging**: Setup two handlers:
   - `StreamHandler(sys.stdout)` — verbose, structured, with timestamps **(F-LOG-020)**
   - `FileHandler("logs/error.log", level=ERROR)` — errors only **(F-LOG-010)**
   - Format: `"%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"`
2. **Instantiate components** (Dependency Injection):
   - `config = ConfigLoader("config/", parquet_dir)`
   - `paths = config.get_paths_config()`
   - `writer = ParquetWriter(paths.parquet_dir)`
   - `failed_store = FailedTickerStore("state/failed_ticker.json")`
   - `resolver = TickerResolver(config, failed_store)`
   - `queue = DownloadQueue()`
   - `rate_limiter = AdaptiveRateLimiter()`
   - `gateway = GatewayClient(config.get_gateway_config())`
3. **Startup sequence**:
   - Run `StartupChecker(paths, writer).run_all_checks()`
   - `await gateway.connect()` — if fails, print error and `sys.exit(1)` **(F-SYS-030)**
   - `await gateway.detect_market_data_type()` **(F-CON-020)**
4. **Start background tasks**:
   - File watcher: periodic `scan_once()` every 5 seconds
   - API server: `uvicorn.run(app, host="0.0.0.0", port=8000)`
   - Download loop: `downloader.run_loop()`
5. **Signal handling**: Register `SIGTERM`/`SIGINT` → call `downloader.stop()`, `gateway.disconnect()`, exit cleanly

**Edge Cases:**
- Missing config files → clear error message + exit(1)
- Gateway unreachable → clear error message + exit(1) (do NOT continue)

---

### T-013 — Docker Deployment

| Field | Value |
|-------|-------|
| **Target File** | `Dockerfile`, `docker-compose.yml`, `requirements.txt` |
| **Description** | Container setup with all volume mounts |
| **Covers** | F-SYS-010 |
| **Context** | No code dependencies |

**Dockerfile Stub:**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
CMD ["python", "src/main.py"]
```

**docker-compose.yml Stub:**

```yaml
version: "3.8"
services:
  stock-data-node:
    build: .
    container_name: stock-data-node
    restart: unless-stopped
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - ./watch:/app/watch
      - ./logs:/app/logs
      - ./state:/app/state
    ports:
      - "8000:8000"
    # network_mode: bridge  # Must reach IB Gateway container
```

**requirements.txt Stub:**

```
ib_insync>=0.9.86
pyarrow>=14.0.0
fastapi>=0.104.0
uvicorn>=0.24.0
```

**Algo/Logic Steps:**
1. Dockerfile: Copy source, install dependencies, set entrypoint.
2. docker-compose: Map all 5 volume mounts. Expose API port. Configure restart policy.
3. requirements.txt: Pin minimum versions for all dependencies.

**Edge Cases:**
- Ensure container can reach IB Gateway container (same Docker network or `network_mode`)

---

### T-014 — Validation Script (IDE Integration Test)

| Field | Value |
|-------|-------|
| **Target File** | `tests/validate_pipeline.py` |
| **Description** | End-to-end validation script runnable from IDE (no Docker). Connects to Gateway, creates watch file, runs download, verifies parquet output |
| **Covers** | Validation of F-CON-010, F-CON-020, F-DAT-010, F-INT-020, F-FNC-030, F-FNC-040, F-FNC-060 |
| **Context** | Imports and wires real implementations of all components. Requires running IB Gateway on localhost. |

**Code Stub:**

```python
"""
Validation Script — Run from IDE (no Docker required).

Prerequisites:
  - IB Gateway or TWS running and authenticated (Paper or Live)
  - pip install ib_insync pyarrow fastapi uvicorn

Usage:
  python tests/validate_pipeline.py

What it does:
  1. Creates temporary config files and directories
  2. Connects to IB Gateway
  3. Detects market data type (Live/Delayed)
  4. Creates a watch file with test tickers
  5. Processes the watch file through the full pipeline
  6. Scans resulting parquet files
  7. Prints sample data from each downloaded ticker
  8. Cleans up (optional)
"""

import asyncio
import sys
import os
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config_loader import ConfigLoader
from gateway_client import GatewayClient
from ticker_resolver import TickerResolver
from priority_queue import DownloadQueue
from downloader import Downloader
from parquet_writer import ParquetWriter
from file_watcher import FileWatcher
from rate_limiter import AdaptiveRateLimiter
from failed_ticker_store import FailedTickerStore
from startup_checks import StartupChecker

# ─── Configuration ────────────────────────────────────────────────

# Tickers to test with (common, liquid US stocks)
TEST_TICKERS = ["AAPL", "MSFT", "GOOG"]

# Gateway connection (adjust to your setup)
GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 4002   # Paper trading default
GATEWAY_MODE = "paper"

# Temporary directories for validation
BASE_DIR = Path(__file__).parent.parent
VALIDATION_DIR = BASE_DIR / "_validation_tmp"

logger = logging.getLogger("validate")


class PipelineValidator:
    """
    Orchestrates a full end-to-end validation of the download pipeline.
    """

    def __init__(self):
        self._config_dir = VALIDATION_DIR / "config"
        self._data_dir = VALIDATION_DIR / "data" / "parquet"
        self._watch_dir = VALIDATION_DIR / "watch"
        self._logs_dir = VALIDATION_DIR / "logs"
        self._state_dir = VALIDATION_DIR / "state"
        # TODO: Implement

    def setup_directories(self) -> None:
        """Creates temp dirs and writes test config files."""
        # TODO: Implement
        ...

    def create_test_configs(self) -> None:
        """Writes gateway.json, paths.json, ticker_map.json."""
        # TODO: Implement
        ...

    def create_watch_file(self) -> None:
        """Creates a watch file with TEST_TICKERS."""
        # TODO: Implement
        ...

    async def run_pipeline(self) -> None:
        """
        Wires all components, runs startup checks,
        connects to gateway, processes watch file,
        runs downloader until queue is empty.
        """
        # TODO: Implement
        ...

    def verify_results(self) -> bool:
        """
        Scans data_dir for parquet files.
        For each: read, validate, print sample rows.
        Returns True if all tickers have data.
        """
        # TODO: Implement
        ...

    def cleanup(self) -> None:
        """Removes _validation_tmp directory."""
        # TODO: Implement
        ...


async def main():
    # TODO: Implement
    ...


if __name__ == "__main__":
    asyncio.run(main())
```

**Algo/Logic Steps:**

1. **`setup_directories()`**: Create `_validation_tmp/` with subdirs: `config/`, `data/parquet/`, `watch/`, `logs/`, `state/`. Use `os.makedirs(exist_ok=True)`.

2. **`create_test_configs()`**: Write these files:
   - `config/gateway.json`:
     ```json
     {"live": {"host": "127.0.0.1", "port": 4001},
      "paper": {"host": "127.0.0.1", "port": 4002},
      "mode": "paper"}
     ```
   - `config/paths.json`:
     ```json
     {"parquet_dir": "<absolute_path>/_validation_tmp/data/parquet",
      "watch_dir": "<absolute_path>/_validation_tmp/watch"}
     ```
   - `config/ticker_map.json`:
     ```json
     {"AAPL": {"symbol": "AAPL", "exchange": "SMART", "currency": "USD", "sec_type": "STK"},
      "MSFT": {"symbol": "MSFT", "exchange": "SMART", "currency": "USD", "sec_type": "STK"},
      "GOOG": {"symbol": "GOOG", "exchange": "SMART", "currency": "USD", "sec_type": "STK"}}
     ```

3. **`create_watch_file()`**: Write `watch/test_validation.txt` with content: `AAPL MSFT GOOG` (space-separated).

4. **`run_pipeline()`**:
   - Configure logging: `StreamHandler(stdout)` with level=DEBUG, format with timestamps
   - Instantiate all components via Dependency Injection:
     ```
     config       = ConfigLoader(config_dir, data_dir)
     writer       = ParquetWriter(data_dir)
     failed_store = FailedTickerStore(state_dir / "failed_ticker.json")
     resolver     = TickerResolver(config, failed_store)
     queue        = DownloadQueue()
     rate_limiter = AdaptiveRateLimiter()
     gateway      = GatewayClient(config.get_gateway_config())
     ```
   - Run `StartupChecker(paths, writer).run_all_checks()`
   - `await gateway.connect()` — if fails → print error and return
   - `await gateway.detect_market_data_type()` → log result
   - Create `FileWatcher` → call `scan_once()` to process the watch file
   - Log: "Queue size after scan: {N}"
   - Create `Downloader` → run `run_loop()` but **stop when queue is empty** (override: check queue.size() == 0 after each processed item, then call `stop()`)
   - `await gateway.disconnect()`
   - Print: "Pipeline complete."

5. **`verify_results()`**:
   - Walk `data_dir`. For each ticker directory:
     - List all `.parquet` files
     - For each parquet: read via `pyarrow.parquet.read_table()`
     - Print header:
       ```
       ════════════════════════════════════════
       TICKER: AAPL | TIMEFRAME: 1D
       Rows: 252 | Date Range: 2025-03-03 → 2026-03-03
       ════════════════════════════════════════
       ```
     - Print first 5 rows and last 5 rows as a formatted table:
       ```
       timestamp           | open    | high    | low     | close   | volume
       2025-03-03 09:30:00 | 178.50  | 179.20  | 177.80  | 178.90  | 52340100
       ...
       ```
     - Track success/failure per ticker
   - Print summary:
     ```
     ══ VALIDATION SUMMARY ══
     ✅ AAPL  — 252 bars (1D)
     ✅ MSFT  — 252 bars (1D)
     ❌ GOOG  — NO DATA
     Result: 2/3 tickers OK
     ```
   - Return `True` if all tickers have at least one parquet with data

6. **`cleanup()`**: `shutil.rmtree(VALIDATION_DIR)` — only if user confirms (print prompt).

7. **`main()`**:
   - Print banner: `"═══ Stock Data Node — Pipeline Validation ═══"`
   - Create `PipelineValidator()`
   - Call `setup_directories()`
   - Call `create_test_configs()`
   - Call `create_watch_file()`
   - `await run_pipeline()`
   - Call `verify_results()`
   - Ask: "Delete validation data? (y/n)" → if y → `cleanup()`

**Edge Cases:**
- IB Gateway not running → clear error: "Cannot connect to IB Gateway at {host}:{port}. Is it running?"
- No market data permissions for a ticker → ticker appears in `failed_ticker.json`, logged as ❌ in summary
- Script interrupted (Ctrl+C) → catch `KeyboardInterrupt`, disconnect gateway, print partial results
- Running the script twice → existing `_validation_tmp/` is reused (idempotent setup)

---

## Dependency Graph

```
T-012 (main.py) ─── orchestrates all ──→ T-001 through T-011
   │
   ├── T-001 (ConfigLoader)       ← no dependencies (reads files)
   ├── T-010 (FailedTickerStore)  ← no dependencies (reads files)
   ├── T-003 (TickerResolver)     ← depends on T-001, T-010
   ├── T-006 (ParquetWriter)      ← no dependencies (reads/writes files)
   ├── T-009 (RateLimiter)        ← no dependencies (pure logic)
   ├── T-004 (PriorityQueue)      ← no dependencies (pure logic)
   ├── T-002 (GatewayClient)      ← depends on T-001
   ├── T-011 (StartupChecker)     ← depends on T-006
   ├── T-005 (Downloader)         ← depends on T-002,T-004,T-006,T-009,T-001,T-010
   ├── T-007 (FileWatcher)        ← depends on T-004,T-003,T-001,T-010
   ├── T-008 (APIServer)          ← depends on T-004,T-003,T-001,T-010
   └── T-014 (Validation)         ← depends on ALL components (T-001 through T-011)
```

**Recommended implementation order:**
`T-001 → T-010 → T-006 → T-009 → T-004 → T-003 → T-002 → T-011 → T-005 → T-007 → T-008 → T-012 → T-013 → T-014`


## PART 3: Extensions (Phase 4)

The following requirements govern the **Optional Ticker Map** feature (T-EXT-001, T-EXT-002, T-EXT-003):

| ID | Title | Description | Covered By |
|----|-------|-------------|------------|
| F-CFG-010 | Optional Ticker Map | Entries in `ticker_map.json` are optional. The absence of a ticker in the map must not lead to immediate blacklisting. | T-EXT-001 |
| F-CFG-020 | Default Contract Inference | If a requested ticker is not defined in the map, the system implicitly generates a default contract: `sec_type="STK"`, `exchange="SMART"`, `currency="USD"`. The input string is used as the `symbol`. | T-EXT-001 |
| F-CFG-030 | Explicit Alias Mapping | The `ticker_map.json` is used primarily for exceptions: differing currencies/exchanges (e.g., EUR at IBIS) or as an alias-mapping where a search term (Key) maps to an actual IBKR symbol (Field `symbol`). | T-EXT-001 |
| F-ERR-010 | API-Driven Blacklisting | A ticker is only added to `failed_ticker.json` (blacklisted) when an API attempt (e.g., contract qualification or download) explicitly fails (e.g., invalid symbol / no definition found). | T-EXT-002 |
| F-ERR-020 | Historical Data Errors | If `ib_insync` emits an `errorEvent` during a historical data request (e.g., Error 162: No market data permissions, 10090: pacing), the error must be captured and raised as an exception to immediately halt the download sequence for that ticker, preventing useless iterative queries for empty periods. | - |
| F-ERR-030 | Invalid Contract (Error 200) | Occurs when the IBKR API does not know the ticker (e.g., delisting, wrong exchange/currency). The system must abort the download immediately on Error 200 and add the ticker to the blacklist (`failed_ticker.json`). | - |
| F-ERR-040 | API Validation (Error 321) | Occurs when the API request is semantically incorrect (e.g., invalid combination of BarSize and Duration). The system must abort on Error 321 and log the error to avoid infinite loops with incorrect parameters. | - |
| F-ERR-050 | Pacing Violation (Error 10090) | Occurs when IBKR's hard rate limits are violated. The system must NOT blacklist the ticker, but must abort the current chunk, wait explicitly (backoff) for several seconds, and then retry. | - |
| F-ERR-060 | No Data For Period (Error 164) | Occurs specifically before an IPO or on holidays where no data exists. The system should treat this period as successfully parsed (empty), but after multiple consecutive failures, abort the historical (Pre-IPO) download. | - |
| F-ERR-070 | Connection Lost (Error 1100) | Occurs when the connection between TWS/Gateway and IBKR servers drops. The downloader must pause (suspend) until Error 1102 (Connection Restored) is received. No tickers should fail just because the connection was temporarily lost. | - |

---

## PART 4: Extensions (Phase 5) - API-Driven Auto-Discovery

The following requirements govern the **API-Driven Auto-Discovery** feature (F-EXT-040 through F-EXT-080). When a ticker is not found in `ticker_map.json`, the system uses `reqMatchingSymbols` to discover the correct exchange and currency, logs it, and persists it.

### 5.1 New Data Structures (`src/models.py`)

```python
@dataclass(frozen=True)
class AutoDiscoveryConfig:
    """Loaded from config/auto_discovery.json (F-EXT-060)."""
    currency_priority: list[str]
    exchange_priority: list[str]
```

### 5.2 Interface Updates

**`IConfigLoader` additions:**
```python
    @abstractmethod
    def get_auto_discovery_config(self) -> AutoDiscoveryConfig:
        """Returns the priorities for auto-discovery."""
        ...

    @abstractmethod
    def update_ticker_map(self, ticker: str, contract: IBKRContract) -> None:
        """Persists a new contract mapping to ticker_map.json and updates the runtime cache. (F-EXT-070)"""
        ...
```

**`IGatewayClient` additions:**
```python
    from ib_insync import ContractDescription

    @abstractmethod
    async def search_contract(self, symbol: str) -> list[ContractDescription]:
        """Wraps reqMatchingSymbolsAsync to find contract alternatives. (F-EXT-040)"""
        ...
```

---

### Implementation Work Orders (Phase 5)

#### T-EXT-004 — Auto-Discovery Config & Map Update
| Field | Value |
|-------|-------|
| **Target File** | `src/config_loader.py`, `src/models.py` |
| **Description** | Implement `AutoDiscoveryConfig` loading and `update_ticker_map()` saving logic. |
| **Covers** | F-EXT-060, F-EXT-070 |
| **Context** | Standard JSON read/write operations within `ConfigLoader`. |

**Algo/Logic Steps:**
1. **`models.py`**: Add `AutoDiscoveryConfig` dataclass and extend `IConfigLoader`.
2. **`ConfigLoader._load_all()`**: Add logic to load `config/auto_discovery.json`. Default to `{"currency_priority": ["USD", "EUR"], "exchange_priority": ["SMART", "IBIS2"]}` if missing.
3. **`ConfigLoader.get_auto_discovery_config()`**: Return the cached config.
4. **`ConfigLoader.update_ticker_map(ticker, contract)`**: 
   - Open `config/ticker_map.json`, read current JSON dict.
   - Insert new key-value pair based on `contract`.
   - Write back to file.
   - Update `self._ticker_map` cache in memory.

#### T-EXT-005 — Gateway Client Symbol Search
| Field | Value |
|-------|-------|
| **Target File** | `src/gateway_client.py` |
| **Description** | Expose the IB API symbol search functionality. |
| **Covers** | F-EXT-040 |
| **Context** | Uses `self._ib.reqMatchingSymbolsAsync`. |

**Algo/Logic Steps:**
1. **`search_contract(symbol)`**: 
   - Call `await self._ib.reqMatchingSymbolsAsync(symbol)`.
   - Return the resulting list of `ContractDescription` objects.
   - Catch exceptions and return `[]` on error.

#### T-EXT-006 — Ticker Resolver Auto-Discovery Integration
| Field | Value |
|-------|-------|
| **Target File** | `src/ticker_resolver.py` |
| **Description** | Intercept unresolved tickers, trigger search, filter, prioritize, and save the result. |
| **Covers** | F-EXT-040, F-EXT-050, F-EXT-060, F-EXT-070, F-EXT-080 |
| **Context** | This requires injecting `IGatewayClient` into `TickerResolver` (or handling it purely in `Downloader`, but `Resolver` is the domain owner of ticker mapping). *Architectural Choice:* Since `search_contract` is async and `TickerResolver.resolve()` is currently sync, it is cleaner to do this in `downloader.py` or make `resolve()` async. Let's make the discovery a dedicated async method in `downloader.py` that gets called before qualification. |

*Correction:* To respect the async boundary, T-EXT-007 will handle the logic inside `downloader.py`.

#### T-EXT-007 — Downloader Auto-Discovery Fallback
| Field | Value |
|-------|-------|
| **Target File** | `src/downloader.py` |
| **Description** | Implement the fallback search when a contract fails to qualify. |
| **Covers** | F-EXT-040, F-EXT-050, F-EXT-060, F-EXT-070, F-EXT-080 |

**Algo/Logic Steps:**
1. Modify `_process_request()` in `downloader.py`.
2. If the initial ticker resolution yields a fallback `SMART/USD` contract (which fails qualification with `QUALIFY_FAILED`):
3. Instead of immediately blacklisting, `await self._auto_discover_contract(ticker)`.
4. **`_auto_discover_contract(ticker)` logic**:
   - `results = await self._gateway.search_contract(ticker)`
   - Filter `results` where `contract.secType == "STK"`. **(F-EXT-050)**
   - If empty → Add to `failed_store` with reason "No STK matches found", return `None`. **(F-EXT-080)**
   - Get `auto_cfg = self._config.get_auto_discovery_config()`.
   - **Sort/Prioritize** the filtered results:
     - Assign a score based on `currency_priority` index (lower is better, fallback to 999 if not in list).
     - Assign a secondary score based on `exchange_priority` index.
     - Sort by `(curr_score, exch_score)`.
   - Pick the winner: `best = sorted_results[0]`.
   - Create `IBKRContract(symbol=best.contract.symbol, exchange=best.contract.primaryExchange or best.contract.exchange, currency=best.contract.currency, sec_type="STK")`.
   - Call `self._config.update_ticker_map(ticker, new_contract)`. **(F-EXT-070)**
   - Log explicit `INFO`: `"Auto-discovered {ticker} -> Exchange: {exchange}, Currency: {currency}"`.
   - Return the new contract.
5. If auto-discovery succeeds, immediately proceed with the download chunk sequence using the new contract.

---

## PART 5: Extensions (Phase 6) - Enhanced Visual Logging

The following requirements govern the **Enhanced Visual Logging** (F-LOG-030 through F-LOG-060). This involves a custom ANSI formatter and structured column alignment.

#### T-015 — Central Logging Formatter & ANSI Colors
| Field | Value |
|-------|-------|
| **Target File** | `src/main.py` (or new `src/logger_utils.py`) |
| **Description** | Implement a custom `logging.Formatter` that handles ANSI colors, Emojis, and fixed-width alignment. |
| **Covers** | F-LOG-030, F-LOG-040, F-LOG-050, F-LOG-060 |
| **Context** | Uses standard Python `logging` module. ANSI codes should be defined as constants. |

**Algo/Logic Steps:**
1. **Define ANSI Constants**:
   - `GREY = "\x1b[38;20m"` (DEBUG)
   - `CYAN = "\x1b[36;20m"` (Variables)
   - `MAGENTA = "\x1b[35;20m"` (Numbers)
   - `YELLOW = "\x1b[33;20m"` (WARNING)
   - `RED = "\x1b[31;20m"` (ERROR)
   - `BOLD_RED = "\x1b[31;1m"` (CRITICAL)
   - `RESET = "\x1b[0m"`
2. **Implement `ColoredFormatter(logging.Formatter)`**:
   - Override `format(record)`:
     - **Columns (F-LOG-050)**: Standardize `%(asctime)s` to `%H:%M:%S`. Pad `levelname` to 8 chars and `name` (module) to 20 chars.
     - **Colors (F-LOG-030)**: Wrap the level name and message in the appropriate ANSI code based on `record.levelno`.
     - **In-Text Highlighting**: Use regex to find capitalized tickers (e.g., `AAPL`) and wrap in `CYAN`. Find numbers/dates and wrap in `MAGENTA`.
     - **ASCII Separators (F-LOG-060)**: If message contains `════`, treat it as a special block and ensure it fills the line. If it starts with `── `, it's a sub-transition.
3. **Update `configure_logging()` in `main.py`**:
   - Replace the standard `logging.Formatter` with `ColoredFormatter` for the `StreamHandler`.
   - Ensure `sys.stdout` is properly flushed or set up for TTY detection (though Docker logs usually support ANSI).

#### T-016 — Integration of Enhanced Logging
| Field | Value |
|-------|-------|
| **Target File** | Multiple: `src/main.py`, `src/downloader.py`, `src/gateway_client.py`, `src/startup_checks.py` |
| **Description** | Update log calls to include the new Emoji dictionary and structural markers. |
| **Covers** | F-LOG-040, F-LOG-060 |

**Algo/Logic Steps:**
1. **Apply Dictionary (F-LOG-040)**:
   - **Startup**: `logger.info("✅ Startup integrity checks complete.")`
   - **Connection**: `logger.info("🌐 Connecting to IB Gateway...")`
   - **Download Start**: `logger.info("▶️ Processing: %s / %s", ticker, timeframe)`
   - **Success**: `logger.info("✅ Written %d bars to %s", count, path)`
   - **Errors**: `logger.error("❌ Qualification failed for %s", ticker)`
2. **Apply Structure (F-LOG-060)**:
   - Use `logger.info("════════════════════════════════════════════════════════════")` for major headers.
   - Use `logger.info("── %s", msg)` for minor steps in the gateway client or downloader.
---

### T-018 — Human-Readable Log Timestamps

| Field | Value |
|-------|-------|
| **Target File** | `src/*.py` (Audit all) |
| **Description** | Ensure no raw Unix timestamps are logged to stdout/stderr. |
| **Covers** | F-LOG-070 |
| **Context** | Applied to all logging calls globally. |

**Algo/Logic Steps:**

1. **Audit**: Search for all `logger.*` calls that include timestamp variables.
2. **Transform**: For any variable that is a Unix timestamp (float/int), wrap it in formatting logic:
   - Use `datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")`.

---

### T-019 — Gateway Client Enhanced Error Hooks
| Field | Value |
|-------|-------|
| **Target File** | `src/gateway_client.py` |
| **Description** | Intercept `errorEvent` during download and raise typed exception payloads or retry indicators. |
| **Covers** | F-ERR-020, F-ERR-030, F-ERR-040, F-ERR-050, F-ERR-060, F-ERR-070 |

**Algo/Logic Steps:**
1. In `_request_historical_bars`, dynamically append an inline `on_error` handler to `self._ib.errorEvent`.
2. Map IBKR error codes to structured python errors (e.g. `RuntimeError` prefixed with the specific code).
   - `162` (No Permissions), `200` (No security definition), `321` (API request invalid).
   - `10090` (Pacing violation).
   - `164` (No data for period).
3. If error 1100/1101/1102 (Connectivity) is raised globally, `gateway_client` must be resilient without crashing.

### T-020 — Downloader Error Categorization & Handling
| Field | Value |
|-------|-------|
| **Target File** | `src/downloader.py` |
| **Description** | Catch exceptions from `GatewayClient`, categorize them explicitly, and trigger the correct control flow (suspend, backoff, abort, empty-success). |
| **Covers** | F-ERR-020, F-ERR-030, F-ERR-040, F-ERR-050, F-ERR-060, F-ERR-070 |

**Algo/Logic Steps:**
1. Add `ErrorCategory.INVALID_CONTRACT` (for 200/321) -> blacklists ticker immediately.
2. Add `ErrorCategory.PACING_VIOLATION` (for 10090) -> NO blacklist. Trigger `rate_limiter.backoff(10 seconds)` and retry chunk.
3. Catch "164 No Data": Treat as successful empty chunk, but if `chunk_ok` exceeds bounds with zero bars total, abort early (Pre-IPO fast-fail).
3. **Verify**: Check `downloader.py` (delta logs), `gateway_client.py` (request logs), and `failed_ticker_store.py` (blacklisting notifications).

**Edge Cases:**
- Ensure timezone is always explicitly `timezone.utc` to avoid local machine discrepancies.
- Do not convert the prefix timestamp (handled by `ColoredFormatter`).

---
