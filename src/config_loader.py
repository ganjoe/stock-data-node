"""
config_loader.py — T-001
Loads all JSON configuration files and supports hot-reload. (F-CFG-010/020/030/040/050)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from models import (
    AutoDiscoveryConfig,
    GatewayConfig,
    GatewayEndpoint,
    IBKRContract,
    IConfigLoader,
    PathsConfig,
    SettingsConfig,
    DownloaderConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAMES = ["1D"]


class ConfigLoader(IConfigLoader):
    """
    Loads gateway.json, paths.json, ticker_map.json from config_dir.
    Per-ticker timeframes are stored in <parquet_dir>/<ticker>/timeframes.json.
    Supports hot-reload via mtime tracking.
    """

    def __init__(self, config_dir: str, parquet_dir: str):
        self._config_dir = Path(config_dir)
        self._parquet_dir = Path(parquet_dir)

        self._gateway_config: Optional[GatewayConfig] = None
        self._paths_config: Optional[PathsConfig] = None
        self._settings_config: Optional[SettingsConfig] = None
        self._downloader_config: Optional[DownloaderConfig] = None
        self._auto_discovery_config: Optional[AutoDiscoveryConfig] = None
        self._ticker_map: dict[str, IBKRContract] = {}

        # Track file modification times for hot-reload
        self._file_mtimes: dict[str, float] = {}

        self._load_all()
        
    @property
    def config_dir(self) -> str:
        return str(self._config_dir)

    # ─── Public Interface ─────────────────────────────────────────

    def get_gateway_config(self) -> GatewayConfig:
        if self._gateway_config is None:
            self._load_gateway()
        return self._gateway_config  # type: ignore[return-value]

    def get_paths_config(self) -> PathsConfig:
        if self._paths_config is None:
            self._load_paths()
        return self._paths_config  # type: ignore[return-value]

    def get_settings_config(self) -> SettingsConfig:
        if self._settings_config is None:
            self._load_settings()
        return self._settings_config  # type: ignore[return-value]

    def get_downloader_config(self) -> DownloaderConfig:
        if self._downloader_config is None:
            self._load_downloader()
        return self._downloader_config  # type: ignore[return-value]

    def get_auto_discovery_config(self) -> AutoDiscoveryConfig:
        if self._auto_discovery_config is None:
            self._load_auto_discovery()
        return self._auto_discovery_config  # type: ignore[return-value]

    def get_ticker_map(self) -> dict[str, IBKRContract]:
        return self._ticker_map

    def get_timeframes_for_ticker(self, ticker: str) -> list[str]:
        """Returns timeframes from <parquet_dir>/<ticker>/timeframes.json or default ["1D"]."""
        tf_file = self._parquet_dir / ticker / "timeframes.json"
        if not tf_file.exists():
            return list(DEFAULT_TIMEFRAMES)
        try:
            with open(tf_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and all(isinstance(t, str) for t in data):
                return data
            logger.error("Invalid timeframes.json for %s — using default", ticker)
            return list(DEFAULT_TIMEFRAMES)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Cannot read timeframes.json for %s: %s — using default", ticker, exc)
            return list(DEFAULT_TIMEFRAMES)

    def add_timeframe_to_ticker(self, ticker: str, timeframe: str) -> None:
        """Appends a timeframe to <parquet_dir>/<ticker>/timeframes.json. (F-CFG-050)"""
        ticker_dir = self._parquet_dir / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)
        current = self.get_timeframes_for_ticker(ticker)
        if timeframe in current:
            return  # Already present
        current.append(timeframe)
        tf_file = ticker_dir / "timeframes.json"
        with open(tf_file, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        logger.info("Added timeframe %s to %s/timeframes.json", timeframe, ticker)

    def register_unmapped_ticker(self, ticker: str) -> None:
        """Adds {ticker: null} to ticker_map.json if not already present. (F-IMP-070)"""
        path = self._config_dir / "ticker_map.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        normalized = ticker.strip().upper()
        if normalized in raw:
            return  # Never overwrite existing mapping (could be manual)

        raw[normalized] = None  # null = unmapped, awaiting manual mapping
        sorted_map = dict(sorted(raw.items()))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted_map, f, indent=2, ensure_ascii=False)
        logger.info(
            "Auto-registered %s as null in ticker_map.json (awaiting manual mapping)",
            normalized,
        )
        # Reload to update in-memory map
        self._load_ticker_map()

    def update_ticker_map(self, ticker: str, contract: IBKRContract) -> None:
        """Persists a new contract mapping to ticker_map.json and updates the runtime cache. (F-EXT-070)"""
        path = self._config_dir / "ticker_map.json"
        
        raw: dict[str, dict[str, str] | None] = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
                
        normalized = ticker.strip().upper()
        raw[normalized] = {
            "symbol": contract.symbol,
            "exchange": contract.exchange,
            "currency": contract.currency,
            "sec_type": contract.sec_type,
        }
        
        sorted_map = dict(sorted(raw.items()))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted_map, f, indent=2, ensure_ascii=False)
            
        logger.info(
            "Saved auto-discovered mapping for %s to ticker_map.json",
            normalized,
        )
        # Reload to update in-memory map
        self._load_ticker_map()

    def reload_if_changed(self) -> None:
        """Checks file mtimes and reloads changed configs. (F-CFG-040)"""
        files = {
            "gateway": self._config_dir / "gateway.json",
            "paths": self._config_dir / "paths.json",
            "settings": self._config_dir / "settings.json",
            "downloader": self._config_dir / "downloader.json",
            "ticker_map": self._config_dir / "ticker_map.json",
            "auto_discovery": self._config_dir / "auto_discovery.json",
        }
        for key, path in files.items():
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            if self._file_mtimes.get(str(path), 0) != mtime:
                logger.info("Config changed, reloading: %s", path.name)
                if key == "gateway":
                    self._load_gateway()
                elif key == "paths":
                    self._load_paths()
                elif key == "settings":
                    self._load_settings()
                elif key == "downloader":
                    self._load_downloader()
                elif key == "ticker_map":
                    self._load_ticker_map()
                elif key == "auto_discovery":
                    self._load_auto_discovery()

    # ─── Private Loaders ─────────────────────────────────────────

    def _load_all(self) -> None:
        self._load_gateway()
        self._load_paths()
        self._load_settings()
        self._load_downloader()
        self._load_auto_discovery()
        self._load_ticker_map()

    def _load_gateway(self) -> None:
        path = self._config_dir / "gateway.json"
        if not path.exists():
            raise FileNotFoundError(
                f"gateway.json not found at {path}. "
                "Create config/gateway.json with 'live', 'paper', and 'mode' keys."
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._gateway_config = GatewayConfig(
            live=GatewayEndpoint(host=raw["live"]["host"], port=raw["live"]["port"]),
            paper=GatewayEndpoint(host=raw["paper"]["host"], port=raw["paper"]["port"]),
            api=GatewayEndpoint(host=raw["api"]["host"], port=raw["api"]["port"]),
            mode=raw["mode"],
        )
        self._file_mtimes[str(path)] = path.stat().st_mtime
        logger.debug("Loaded gateway.json (mode=%s)", raw["mode"])

    def _load_paths(self) -> None:
        path = self._config_dir / "paths.json"
        if not path.exists():
            raise FileNotFoundError(
                f"paths.json not found at {path}. "
                "Create config/paths.json with 'parquet_dir' and 'watch_dir' keys."
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        base_dir = self._config_dir.parent
        self._paths_config = PathsConfig(
            parquet_dir=str(base_dir / raw["parquet_dir"]),
            watch_dir=str(base_dir / raw["watch_dir"]),
            watchlist_dir=str(base_dir / raw.get("watchlist_dir", "data/watchlists")),
        )
        self._file_mtimes[str(path)] = path.stat().st_mtime
        logger.debug("Loaded paths.json")

    def _load_settings(self) -> None:
        path = self._config_dir / "settings.json"
        if not path.exists():
            # Provide sensible defaults if settings.json is missing
            self._settings_config = SettingsConfig(
                file_watcher_interval=5.0,
                gateway_connect_timeout=20,
                market_data_type_wait=0.5,
                market_data_snapshot_wait=2.0,
                processing_threads=4,
            )
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._settings_config = SettingsConfig(
            file_watcher_interval=float(raw.get("file_watcher_interval", 5.0)),
            gateway_connect_timeout=int(raw.get("gateway_connect_timeout", 20)),
            market_data_type_wait=float(raw.get("market_data_type_wait", 0.5)),
            market_data_snapshot_wait=float(raw.get("market_data_snapshot_wait", 2.0)),
            processing_threads=int(raw.get("processing_threads", 4)),
            live_max_concurrent=int(raw.get("live_max_concurrent", 20)),
            live_pacing_delay=float(raw.get("live_pacing_delay", 0.1)),
            delayed_max_concurrent=int(raw.get("delayed_max_concurrent", 1)),
            delayed_pacing_delay=float(raw.get("delayed_pacing_delay", 3.0)),
        )
        self._file_mtimes[str(path)] = path.stat().st_mtime
        logger.debug("Loaded settings.json")

    def _load_downloader(self) -> None:
        path = self._config_dir / "downloader.json"
        if not path.exists():
            # Provide sensible defaults if downloader.json is missing
            self._downloader_config = DownloaderConfig(
                chunk_duration={
                    "1m": 86400, "5m": 604800, "15m": 1209600, "30m": 1209600,
                    "1h": 2592000, "4h": 7776000, "1D": 31536000, "1W": 157680000,
                    "1M": 315360000, "default": 2592000
                },
                max_history_lookback={
                    "1m": 2592000, "5m": 7776000, "15m": 15552000, "30m": 15552000,
                    "1h": 31536000, "4h": 63072000, "1D": 630720000, "1W": 630720000,
                    "1M": 630720000
                }
            )
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._downloader_config = DownloaderConfig(
            chunk_duration=raw.get("chunk_duration", {}),
            max_history_lookback=raw.get("max_history_lookback", {}),
        )
        self._file_mtimes[str(path)] = path.stat().st_mtime
        logger.debug("Loaded downloader.json")

    def _load_auto_discovery(self) -> None:
        path = self._config_dir / "auto_discovery.json"
        if not path.exists():
            # Apply sensible defaults if optional config is missing
            self._auto_discovery_config = AutoDiscoveryConfig(
                currency_priority=["USD", "EUR"],
                exchange_priority=["SMART", "IBIS2"]
            )
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
            
        self._auto_discovery_config = AutoDiscoveryConfig(
            currency_priority=raw.get("currency_priority", ["USD", "EUR"]),
            exchange_priority=raw.get("exchange_priority", ["SMART", "IBIS2"]),
        )
        self._file_mtimes[str(path)] = path.stat().st_mtime
        logger.debug("Loaded auto_discovery.json")

    def _load_ticker_map(self) -> None:
        path = self._config_dir / "ticker_map.json"
        if not path.exists():
            raise FileNotFoundError(
                f"ticker_map.json not found at {path}. "
                "Create config/ticker_map.json with ticker → contract mappings."
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        self._ticker_map = {}
        null_count = 0
        for symbol, data in raw.items():
            key = symbol.upper()
            if data is None:
                # null = unmapped/SKIP sentinel (F-IMP-070)
                self._ticker_map[key] = IBKRContract(
                    symbol="SKIP", exchange="", currency="", sec_type=""
                )
                null_count += 1
            else:
                self._ticker_map[key] = IBKRContract(
                    symbol=data["symbol"],
                    exchange=data["exchange"],
                    currency=data["currency"],
                    sec_type=data["sec_type"],
                )

        self._file_mtimes[str(path)] = path.stat().st_mtime
        logger.debug(
            "Loaded ticker_map.json (%s tickers, %s unmapped/null)",
            f"{len(self._ticker_map):,d}".replace(",", "."),
            f"{null_count:,d}".replace(",", "."),
        )
