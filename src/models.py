"""
models.py — System Skeleton (Shared Context, Read-Only)
All data structures, enums, and interfaces for the stock-data-node project.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Optional

from ib_insync import ContractDescription


# ─── Enums ────────────────────────────────────────────────────────

class MarketDataType(IntEnum):
    """IBKR Market Data Types."""
    LIVE = 1
    FROZEN = 2
    DELAYED = 3
    DELAYED_FROZEN = 4


class DownloadPriority(IntEnum):
    """Queue priority levels. Lower value = higher priority."""
    API = 1        # REST-API requests (highest)
    WATCHER = 2    # File-watcher requests


class TickerStatus(str, Enum):
    """Processing status of a ticker in the queue."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"   # in failed_ticker.json blacklist


class TimeframePhase(str, Enum):
    """Bulk download phase ordering."""
    DAILY = "daily"           # Phase 1: all tickers 1D
    SECONDARY = "secondary"   # Phase 2: remaining timeframes


class ErrorCategory(str, Enum):
    """Fine-grained IBKR error classification. (F-IMP-060)"""
    QUALIFY_FAILED = "QUALIFY_FAILED"
    NO_DATA = "NO_DATA"
    NO_PERMISSIONS = "NO_PERMISSIONS"
    TIMEOUT = "TIMEOUT"
    PACING = "PACING"
    CANCELLED = "CANCELLED"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    UNKNOWN = "UNKNOWN"


class MarketStatus(str, Enum):
    """NYSE market status. (F-IMP-080)"""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PRE_MARKET = "PRE-MARKET"
    POST_MARKET = "POST-MARKET"
    CLOSED_WEEKEND = "CLOSED (WEEKEND)"
    CLOSED_HOLIDAY = "CLOSED (HOLIDAY)"


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
    watchlist_dir: str   # e.g. "/app/data/watchlists"


@dataclass(frozen=True)
class AutoDiscoveryConfig:
    """Loaded from config/auto_discovery.json. (F-EXT-060)"""
    currency_priority: list[str]
    exchange_priority: list[str]


@dataclass
class IBKRContract:
    """Single entry from config/ticker_map.json. (F-FNC-040)"""
    symbol: str
    exchange: str
    currency: str
    sec_type: str        # "STK", "FUT", etc.


@dataclass(frozen=True)
class BatchConfig:
    """Runtime configuration adapted to detected market data permissions. (F-IMP-010, F-IMP-020)"""
    market_data_type: MarketDataType
    max_concurrent: int       # Live=20, Delayed=1
    base_pacing_delay: float  # Live=0.1, Delayed=3.0, Fallback=5.0
    description: str          # Human-readable label


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

    def __repr__(self) -> str:
        dt = datetime.fromtimestamp(self.created_at, tz=timezone.utc).strftime("%H:%M:%S")
        return f"DownloadRequest({self.ticker}/{self.timeframe}, prio={self.priority.name}, created={dt})"

    def __lt__(self, other: DownloadRequest) -> bool:
        """For heapq ordering: lower priority value = higher priority."""
        return (int(self.priority), self.created_at) < (int(other.priority), other.created_at)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DownloadRequest):
            return NotImplemented
        return (int(self.priority), self.created_at) == (int(other.priority), other.created_at)


@dataclass
class DownloadChunk:
    """A time-bounded chunk of a historical download. (F-FNC-020)"""
    ticker: str
    timeframe: str
    start_ts: int        # Unix timestamp
    end_ts: int          # Unix timestamp
    is_final: bool       # True if this is the last chunk

    def __repr__(self) -> str:
        s = datetime.fromtimestamp(self.start_ts, tz=timezone.utc).strftime("%d.%m.%Y")
        e = datetime.fromtimestamp(self.end_ts, tz=timezone.utc).strftime("%d.%m.%Y")
        return f"DownloadChunk({self.ticker}/{self.timeframe}, {s} to {e})"


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


# ─── Interfaces (Abstract Base Classes) ──────────────────────────

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

    @abstractmethod
    def register_unmapped_ticker(self, ticker: str) -> None:
        """Adds {ticker: null} to ticker_map.json if not already present. (F-IMP-070)"""
        ...

    @abstractmethod
    def get_auto_discovery_config(self) -> AutoDiscoveryConfig:
        """Returns the priorities for auto-discovery."""
        ...

    @abstractmethod
    def update_ticker_map(self, ticker: str, contract: IBKRContract) -> None:
        """Persists a new contract mapping to ticker_map.json and updates the runtime cache. (F-EXT-070)"""
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
    async def detect_market_data_type(self) -> BatchConfig:
        """Detects best market data type and returns adaptive config. (F-CON-020, F-IMP-010/020)"""
        ...

    @abstractmethod
    async def request_historical_bars(
        self, contract: IBKRContract, timeframe: str,
        start_ts: int, end_ts: int
    ) -> list[OHLCVBar]:
        """Downloads historical bars for a single chunk."""
        ...

    @abstractmethod
    async def qualify_contracts_batch(
        self, contracts: dict[str, IBKRContract]
    ) -> dict[str, IBKRContract]:
        """Qualifies all contracts in a single batch call. (F-IMP-040)"""
        ...

    @abstractmethod
    async def search_contract(self, symbol: str) -> list[ContractDescription]:
        """Wraps reqMatchingSymbolsAsync to find contract alternatives. (F-EXT-040)"""
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
    def is_ignored(self, ticker: str) -> bool:
        """Returns True if the ticker is explicitly blacklisted or mapped to SKIP."""
        ...

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

    @abstractmethod
    def check_year_coverage(self, ticker: str, timeframe: str) -> set[int]:
        """Returns set of years with >200 bars (fully covered). (F-IMP-030)"""
        ...


class IRateLimiter(ABC):
    """Adaptive rate limiting with backoff. (F-FNC-050, F-IMP-010/020)"""

    @abstractmethod
    def configure(self, config: BatchConfig) -> None:
        """Applies BatchConfig pacing and concurrency settings. (F-IMP-020)"""
        ...

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
    def remove(self, ticker: str) -> None: ...

    @abstractmethod
    def load(self) -> list[FailedTickerEntry]: ...
