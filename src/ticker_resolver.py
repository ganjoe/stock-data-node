"""
ticker_resolver.py — T-003
Resolves ticker symbols to IBKR contracts, checks blacklist. (F-FNC-040, F-FNC-070)
"""
from __future__ import annotations

import logging
from typing import Optional

from models import IBKRContract, IConfigLoader, IFailedTickerStore, ITickerResolver

logger = logging.getLogger(__name__)


class TickerResolver(ITickerResolver):
    """
    Resolves ticker strings to IBKRContract objects.
    Always checks the blacklist first — blacklisted tickers are silently skipped.
    """

    def __init__(self, config: IConfigLoader, failed_store: IFailedTickerStore) -> None:
        self._config = config
        self._failed_store = failed_store

    def resolve(self, ticker: str) -> Optional[IBKRContract]:
        """
        Returns the IBKRContract for a ticker symbol, or None if:
        - ticker is empty / invalid
        - ticker is in the failed/blacklist
        - ticker is not found in ticker_map.json
        - ticker is mapped to null (SKIP sentinel, F-IMP-070)
        """
        if not ticker or not ticker.strip():
            return None

        normalized = ticker.strip().upper()

        # Check blacklist (re-reads from disk so manual edits take effect)
        if self._failed_store.is_blacklisted(normalized):
            logger.info("Skipping blacklisted ticker: %s", normalized)
            return None

        # Hot-reload ticker_map if the file changed on disk
        self._config.reload_if_changed()

        contract = self._config.get_ticker_map().get(normalized)
        if contract is None:
            logger.info(
                "Ticker %s not mapped — using default fallback (SMART/USD/STK)",
                normalized
            )
            return IBKRContract(
                symbol=normalized,
                exchange="SMART",
                currency="USD",
                sec_type="STK"
            )

        # SKIP sentinel: ticker_map has null mapping (F-IMP-070)
        if contract.symbol == "SKIP":
            logger.info(
                "Ticker %s mapped to null — skipping (awaiting manual mapping)",
                normalized,
            )
            return None

        return contract
