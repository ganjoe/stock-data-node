# IMP_alm_requirements_opt — Implementation Specification (Downloader Optimization)

> **Source:** `alm_requirements` (F-OPT-020 … F-OPT-070)
> **Author:** Senior Software Architect (AI-Optimized Workflow)
> **Target:** Junior AI Agents (atomic, isolated tasks)
> **Language:** Python 3.11+ | **Libraries:** `ib_insync`, `asyncio`

---

## PART 1: The System Skeleton (Shared Context)

> This section defines the **new or modified** data structures that extend the existing `src/models.py`.
> All existing data structures from `src/models.py` remain unchanged unless explicitly modified below.
> This code is **read-only context** for all Junior AI Agents.

### 1.1 Modified Data Structure: `SettingsConfig`

The existing `SettingsConfig` (in `src/models.py`) must be extended with new fields for the optimization parameters. Default values ensure backwards-compatibility.

```python
@dataclass(frozen=True)
class SettingsConfig:
    """Loaded from config/settings.json. (F-CFG-030, F-OPT-020/030/050/070)"""
    file_watcher_interval: float
    gateway_connect_timeout: int
    market_data_type_wait: float
    market_data_snapshot_wait: float
    processing_threads: int

    # Existing batching parameters (F-IMP-010, F-IMP-020)
    live_max_concurrent: int = 20
    live_pacing_delay: float = 0.1
    delayed_max_concurrent: int = 15        # ← CHANGED from 1 (F-OPT-020)
    delayed_pacing_delay: float = 0.5       # ← CHANGED from 3.0 (optimistic start, dynamic throttling covers safety)

    # NEW: Dynamic Semaphore Throttling (F-OPT-020)
    throttle_recovery_threshold: int = 5    # successful requests before increasing semaphore by 1
    throttle_min_concurrent: int = 1        # floor: never go below this

    # NEW: Identical Request Debounce (F-OPT-030)
    request_debounce_seconds: float = 15.0  # minimum interval for identical requests

    # NEW: Idle Connection Watchdog (F-OPT-050)
    connection_watchdog_interval: float = 600.0  # seconds (10 min)

    # NEW: Performance-Optimized Logging (F-OPT-070)
    bulk_log_level: str = "INFO"            # logging level during bulk downloads
```

### 1.2 Modified Data Structure: `DownloaderConfig`

The existing `DownloaderConfig` (in `src/models.py`) must be extended with max chunk sizes per timeframe.

```python
@dataclass(frozen=True)
class DownloaderConfig:
    """Loaded from config/downloader.json. (F-CFG-030, F-OPT-040)"""
    chunk_duration: dict[str, int]
    max_history_lookback: dict[str, int]
    max_chunk_size: dict[str, int]          # ← NEW (F-OPT-040): max seconds per request per timeframe
```

### 1.3 New Data Structure: `RequestFingerprint`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class RequestFingerprint:
    """Unique identifier for an API request, used for debounce tracking. (F-OPT-030)"""
    symbol: str
    timeframe: str
    start_ts: int
    end_ts: int
```

### 1.4 Modified Interface: `IRateLimiter`

The existing `IRateLimiter` interface (in `src/models.py`) must be extended:

```python
class IRateLimiter(ABC):
    """Adaptive rate limiting with backoff and dynamic throttling. (F-FNC-050, F-IMP-010/020, F-OPT-020/030)"""

    @abstractmethod
    def configure(self, config: BatchConfig, settings: SettingsConfig) -> None:
        """Applies BatchConfig pacing/concurrency AND SettingsConfig throttle params. (F-IMP-020, F-OPT-020)"""
        ...

    @abstractmethod
    async def acquire(self) -> None:
        """Acquires semaphore slot, then waits pacing delay."""
        ...

    @abstractmethod
    def report_pacing_error(self) -> None:
        """Reduces semaphore AND doubles delay. (F-OPT-020)"""
        ...

    @abstractmethod
    def report_success(self) -> None:
        """Increments semaphore after N consecutive successes. (F-OPT-020)"""
        ...

    @abstractmethod
    def check_debounce(self, fingerprint: RequestFingerprint) -> bool:
        """Returns True if the request should be BLOCKED (sent too recently). (F-OPT-030)"""
        ...

    @abstractmethod
    def record_request(self, fingerprint: RequestFingerprint) -> None:
        """Records that a request was sent (for debounce tracking). (F-OPT-030)"""
        ...
```

### 1.5 Modified Interface: `IGatewayClient`

The existing `IGatewayClient` interface (in `src/models.py`) must be extended:

```python
class IGatewayClient(ABC):
    """Manages IBKR Gateway connection. (F-CON-010/020, F-OPT-050)"""

    # ... (all existing abstract methods remain unchanged)

    @abstractmethod
    async def ensure_connected(self) -> None:
        """Proactively checks connection health and reconnects if stale. (F-OPT-050)"""
        ...
```

---

## PART 2: Implementation Work Orders

---

### T-OPT-001 — Dynamic Semaphore Throttling (Rate Limiter Rewrite)

| Field | Value |
|-------|-------|
| **Target File** | `src/rate_limiter.py` |
| **Description** | Rewrite the rate limiter with dynamic semaphore adjustment and request debounce |
| **Covers** | F-OPT-020, F-OPT-030 |
| **Context** | Rewrites existing `AdaptiveRateLimiter`. Uses `BatchConfig`, `SettingsConfig`, `RequestFingerprint`, `IRateLimiter` |

**Code Stub:**

```python
import asyncio
import logging
import time
from typing import Optional

from models import BatchConfig, IRateLimiter, RequestFingerprint, SettingsConfig

logger = logging.getLogger(__name__)


class AdaptiveRateLimiter(IRateLimiter):
    """
    Adaptive rate limiter with:
    - Dynamic semaphore: shrinks on pacing errors, grows on success (F-OPT-020)
    - Exponential backoff on pacing violations
    - Request debounce to prevent identical requests within 15s (F-OPT-030)
    """

    MAX_BACKOFF = 300.0        # 5 min maximum backoff
    BACKOFF_FACTOR = 2.0
    DEFAULT_DELAY = 10.0       # safe default before configure() is called

    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._base_delay: float = self.DEFAULT_DELAY
        self._current_delay: float = self.DEFAULT_DELAY
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)

        # Dynamic throttling state (F-OPT-020)
        self._max_concurrent: int = 1
        self._current_concurrent: int = 1
        self._min_concurrent: int = 1
        self._consecutive_successes: int = 0
        self._recovery_threshold: int = 5

        # Debounce state (F-OPT-030)
        self._debounce_seconds: float = 15.0
        self._request_history: dict[RequestFingerprint, float] = {}

    def configure(self, config: BatchConfig, settings: SettingsConfig) -> None:
        # TODO: Implement
        ...

    async def acquire(self) -> None:
        # TODO: Implement
        ...

    def report_pacing_error(self) -> None:
        # TODO: Implement
        ...

    def report_success(self) -> None:
        # TODO: Implement
        ...

    def check_debounce(self, fingerprint: RequestFingerprint) -> bool:
        # TODO: Implement
        ...

    def record_request(self, fingerprint: RequestFingerprint) -> None:
        # TODO: Implement
        ...

    def _rebuild_semaphore(self, new_limit: int) -> None:
        """Replaces the semaphore with a new one at the given limit."""
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **`configure(config, settings)`**:
   - Store `config.max_concurrent` as `self._max_concurrent` and `self._current_concurrent`.
   - Store `settings.throttle_min_concurrent` as `self._min_concurrent`.
   - Store `settings.throttle_recovery_threshold` as `self._recovery_threshold`.
   - Store `settings.request_debounce_seconds` as `self._debounce_seconds`.
   - Set `self._base_delay = config.base_pacing_delay`.
   - Set `self._current_delay = config.base_pacing_delay`.
   - Create `self._semaphore = asyncio.Semaphore(config.max_concurrent)`.
   - Reset `self._consecutive_successes = 0`.
   - Log: "Rate limiter configured: pacing={delay}s, concurrent={max} ({description})".

2. **`acquire()`**:
   - `await self._semaphore.acquire()`.
   - Calculate `elapsed = time.monotonic() - self._last_request_time`.
   - `wait = self._current_delay - elapsed`, if wait > 0 → `await asyncio.sleep(wait)`.
   - Update `self._last_request_time = time.monotonic()`.
   - **Important:** Do NOT release the semaphore here. The caller is responsible for ensuring `release()` is called (or use acquire/release in a context-manager pattern). However, for backwards-compatibility with the existing pattern where `acquire()` also releases, keep the existing behavior: acquire then release within the method. The semaphore acts as concurrency-guard for the pacing-sleep section only.

3. **`report_pacing_error()`** (F-OPT-020):
   - Reset `self._consecutive_successes = 0`.
   - Double `self._current_delay = min(self._current_delay * BACKOFF_FACTOR, MAX_BACKOFF)`.
   - **Reduce concurrency:** `new_limit = max(self._min_concurrent, self._current_concurrent - 1)`.
   - If `new_limit != self._current_concurrent`: call `self._rebuild_semaphore(new_limit)`, update `self._current_concurrent`.
   - Log: "⚠️ Pacing error — backoff {old}s → {new}s, concurrency {old_c} → {new_c}".

4. **`report_success()`** (F-OPT-020):
   - Increment `self._consecutive_successes`.
   - If `self._current_delay != self._base_delay`: reset `self._current_delay = self._base_delay` and log reset.
   - If `self._consecutive_successes >= self._recovery_threshold` AND `self._current_concurrent < self._max_concurrent`:
     - `new_limit = self._current_concurrent + 1`.
     - Call `self._rebuild_semaphore(new_limit)`, update `self._current_concurrent`.
     - Reset `self._consecutive_successes = 0`.
     - Log: "✅ Throttle recovery: concurrency {old} → {new}".

5. **`check_debounce(fingerprint)`** (F-OPT-030):
   - If `fingerprint` is in `self._request_history`:
     - `last_time = self._request_history[fingerprint]`.
     - If `time.monotonic() - last_time < self._debounce_seconds` → return `True` (BLOCKED).
   - Return `False` (OK to proceed).

6. **`record_request(fingerprint)`** (F-OPT-030):
   - `self._request_history[fingerprint] = time.monotonic()`.
   - **Housekeeping:** Remove entries older than `self._debounce_seconds * 2` to prevent memory leaks. Iterate over a copy of keys, remove stale entries.

7. **`_rebuild_semaphore(new_limit)`**:
   - Create a new `asyncio.Semaphore(new_limit)`.
   - Replace `self._semaphore` with the new one.
   - Log the change.

**Edge Cases:**
- `configure()` not called yet → safe defaults (10s delay, 1 concurrent)
- `_rebuild_semaphore` during active `acquire()` calls → new semaphore only affects future callers, no race condition because asyncio is single-threaded
- Debounce history memory leak → housekeeping purge in `record_request()`

---

### T-OPT-002 — Max Chunk Sizes (Downloader Config Extension)

| Field | Value |
|-------|-------|
| **Target File** | `config/downloader.json`, `src/config_loader.py`, `src/models.py` |
| **Description** | Add `max_chunk_size` configuration and enforce it during chunk calculation |
| **Covers** | F-OPT-040 |
| **Context** | Extends `DownloaderConfig`, modifies `ConfigLoader._load_downloader_config()`, modifies `Downloader._calculate_chunks()` |

**Algo/Logic Steps:**

1. **`config/downloader.json`** — Add a new top-level key:
   ```json
   {
       "chunk_duration": { ... },
       "max_history_lookback": { ... },
       "max_chunk_size": {
           "1m": 172800,
           "5m": 604800,
           "15m": 1209600,
           "30m": 2592000,
           "1h": 2592000,
           "4h": 7776000,
           "1D": 31536000,
           "1W": 157680000,
           "1M": 315360000,
           "default": 2592000
       }
   }
   ```
   - `1m = 172800` → 2 days in seconds (F-OPT-040: Max 2 Tage für 1-Minute-Bars)
   - `1h = 2592000` → 30 days in seconds (F-OPT-040: Max 1 Monat für 1-Stunden-Bars)

2. **`src/models.py`** — Extend parsing in `DownloaderConfig`:
   - Add `max_chunk_size: dict[str, int]` field.

3. **`src/config_loader.py`** — In the method that loads `downloader.json`:
   - Parse `max_chunk_size` from JSON.
   - Provide empty dict as default if key is missing (backwards-compatible).

4. **`src/downloader.py`** — In `_calculate_chunks()`:
   - After determining `chunk_size` from `dl_cfg.chunk_duration`, look up `max_size = dl_cfg.max_chunk_size.get(timeframe, dl_cfg.max_chunk_size.get("default", chunk_size))`.
   - Use `effective_chunk = min(chunk_size, max_size)` as the actual chunk duration.
   - This ensures no single request exceeds the IBKR limit for that timeframe.

**Edge Cases:**
- `max_chunk_size` key missing from JSON → use `chunk_duration` as-is (no capping)
- A timeframe not found in `max_chunk_size` → fall back to `"default"` key, then to `chunk_size`

---

### T-OPT-003 — Idle Connection Watchdog

| Field | Value |
|-------|-------|
| **Target File** | `src/gateway_client.py` |
| **Description** | Add proactive connection health check for long-running bulk downloads |
| **Covers** | F-OPT-050 |
| **Context** | Extends `GatewayClient`. Uses `SettingsConfig` (for `connection_watchdog_interval`) |

**Code Stub:**

```python
# Add to existing GatewayClient class:

    async def ensure_connected(self) -> None:
        """
        Proactively checks connection health and reconnects if stale. (F-OPT-050)
        """
        # TODO: Implement
        ...
```

**Algo/Logic Steps:**

1. **Track last activity:** Add `self._last_activity_time: float = time.monotonic()` to `__init__`.
2. **Update on success:** In `request_historical_bars()`, after each successful response, update `self._last_activity_time = time.monotonic()`.
3. **`ensure_connected()`**:
   - If `self._ib.isConnected()` is `False`:
     - Log: "⚠️ Connection lost — attempting reconnect..."
     - Call `await self.connect()`.
     - Return.
   - Calculate `idle_time = time.monotonic() - self._last_activity_time`.
   - If `idle_time` exceeds the watchdog interval (received from config or stored during init):
     - Log: "⚠️ Connection idle for {idle_time:.0f}s — sending heartbeat..."
     - Send a lightweight request to verify the connection is still alive. Use `self._ib.reqCurrentTime()` (returns the server time, very cheap).
     - If this raises an exception → the connection is dead:
       - Log: "❌ Connection is stale — reconnecting..."
       - Call `self._ib.disconnect()` then `await self.connect()`.
     - Update `self._last_activity_time`.
4. **Integration in Downloader:** The `Downloader.run_loop()` must call `await self._gateway.ensure_connected()` before processing each request.

**Edge Cases:**
- `reqCurrentTime()` succeeds but returns garbage → treat as alive (IBKR guarantees valid timestamps)
- Reconnect fails → `ConnectionError` propagates up to `run_loop`, which will re-try after sleep
- Initial state: `_last_activity_time = time.monotonic()` set at `connect()` time, not at `__init__`

---

### T-OPT-004 — Downloader Integration (Debounce + Watchdog Wiring)

| Field | Value |
|-------|-------|
| **Target File** | `src/downloader.py` |
| **Description** | Wire debounce checks and watchdog into the download loop |
| **Covers** | F-OPT-030, F-OPT-050 |
| **Context** | Modifies `Downloader._process_request()` and `Downloader._download_chunk()`. Uses `RequestFingerprint`, updated `IRateLimiter` |

**Algo/Logic Steps:**

1. **Debounce in `_download_chunk()`** (F-OPT-030):
   - Before calling `self._gateway.request_historical_bars()`:
     - Create `fingerprint = RequestFingerprint(symbol=contract.symbol, timeframe=chunk.timeframe, start_ts=chunk.start_ts, end_ts=chunk.end_ts)`.
     - Call `if self._rate_limiter.check_debounce(fingerprint)`:
       - Log: "⏭️ Debounce: skipping duplicate request for {contract.symbol}/{chunk.timeframe}"
       - Return `DownloadResult(...)` with `error=None` and `bars=[]` (treated as empty-success, not an error).
   - After successfully sending the request (regardless of outcome):
     - Call `self._rate_limiter.record_request(fingerprint)`.

2. **Watchdog in `run_loop()`** (F-OPT-050):
   - Before processing each request (after `dequeue()`), call:
     - `await self._gateway.ensure_connected()`.
   - This ensures stale connections are detected and repaired before any download attempt.

**Edge Cases:**
- Debounce triggers on retries → If chunk was retried after pacing error, the identical request might be debounced. To handle this: debounce should be checked BEFORE the first attempt, but NOT before a retry. Pass a `is_retry: bool` parameter or clear the specific fingerprint before retry.

---

### T-OPT-005 — Config Extensions (settings.json + Models)

| Field | Value |
|-------|-------|
| **Target File** | `config/settings.json`, `src/models.py`, `src/config_loader.py` |
| **Description** | Extend settings.json with new optimization parameters, update SettingsConfig parsing |
| **Covers** | F-OPT-020, F-OPT-030, F-OPT-050, F-OPT-070 |
| **Context** | Extends `SettingsConfig` dataclass and `ConfigLoader._load_settings()` |

**Algo/Logic Steps:**

1. **`config/settings.json`** — Add new keys (all with safe defaults):
   ```json
   {
       "file_watcher_interval": 5.0,
       "gateway_connect_timeout": 20,
       "market_data_type_wait": 0.5,
       "market_data_snapshot_wait": 2.0,
       "processing_threads": 4,
       "live_max_concurrent": 20,
       "live_pacing_delay": 0.1,
       "delayed_max_concurrent": 15,
       "delayed_pacing_delay": 0.5,
       "throttle_recovery_threshold": 5,
       "throttle_min_concurrent": 1,
       "request_debounce_seconds": 15.0,
       "connection_watchdog_interval": 600.0,
       "bulk_log_level": "INFO"
   }
   ```

2. **`src/models.py`** — Extend `SettingsConfig` as shown in PART 1 (Section 1.1).

3. **`src/config_loader.py`** — In `_load_settings()`:
   - Parse the new keys from JSON with default values:
     - `throttle_recovery_threshold`: default `5`
     - `throttle_min_concurrent`: default `1`
     - `request_debounce_seconds`: default `15.0`
     - `connection_watchdog_interval`: default `600.0`
     - `bulk_log_level`: default `"INFO"`
   - Backwards-compatible: If key is missing from JSON → use the default value from the dataclass.

4. **`src/main.py`** — Wire `configure()` call:
   - Currently: `rate_limiter.configure(batch_config)`.
   - Change to: `rate_limiter.configure(batch_config, config.get_settings_config())`.

5. **`src/main.py`** — Performance-Optimized Logging (F-OPT-070):
   - After `batch_config` is determined, read `settings.bulk_log_level`.
   - Set `logging.getLogger("ib_insync").setLevel(getattr(logging, settings.bulk_log_level, logging.INFO))`.
   - Set the root logger stream handler to `getattr(logging, settings.bulk_log_level, logging.INFO)`.
   - Log: "ℹ️ Bulk logging level set to {bulk_log_level}".

**Edge Cases:**
- Old `settings.json` without new keys → dataclass defaults kick in, no crash
- Invalid `bulk_log_level` string → fall back to `logging.INFO`

---

### T-OPT-006 — Gateway Memory Optimization (Documentation)

| Field | Value |
|-------|-------|
| **Target File** | `docker-compose.yml` (documentation/comments), `readme.txt` |
| **Description** | Document JVM Heap-Speicher recommendation for IB Gateway |
| **Covers** | F-OPT-060 |
| **Context** | This is a documentation/infrastructure task, not a code change |

**Algo/Logic Steps:**

1. **`docker-compose.yml`** — If the IB Gateway is configured as a Docker service:
   - Add environment variable `JAVA_HEAP_SIZE=4096` (or equivalent JVM flag).
   - Add a comment: `# F-OPT-060: Gateway JVM heap ≥ 4096 MB for bulk data downloads`.
2. **`readme.txt`** — Add a section "Performance Tuning":
   - "Set IB Gateway JVM Heap to at least 4096 MB (`-Xmx4096m`) for optimized bulk downloads."
   - "Set Gateway Logging Level to INFO (avoid DEBUG) to reduce CPU overhead during mass data decoding."

**Edge Cases:**
- If IB Gateway is external (not managed by this project) → document the recommendation in `readme.txt` only.

---

## Dependency Graph

```
T-OPT-005 (Config)  ──────────────────────────────┐
    │                                              │
    ├───→ T-OPT-001 (Rate Limiter Rewrite)         │
    │         │                                    │
    │         └───→ T-OPT-004 (Downloader Wiring)  │
    │                    │                          │
    ├───→ T-OPT-002 (Chunk Sizes) ──→ Downloader ──┘
    │
    └───→ T-OPT-003 (Watchdog) ──→ T-OPT-004

T-OPT-006 (Documentation) → independent, can run in parallel
```

**Execution Order:**
1. **T-OPT-005** (Config) — prerequisite for all others
2. **T-OPT-001** (Rate Limiter) + **T-OPT-002** (Chunk Sizes) + **T-OPT-003** (Watchdog) — can be parallelized
3. **T-OPT-004** (Downloader Wiring) — depends on T-OPT-001, T-OPT-003
4. **T-OPT-006** (Documentation) — independent, any time
