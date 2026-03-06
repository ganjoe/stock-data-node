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

    def is_ignored(self, ticker: str) -> bool:
        """Returns True if the ticker is blacklisted or sentinel SKIP."""
        if not ticker or not ticker.strip():
            return True
        normalized = ticker.strip().upper()
        if self._failed_store.is_blacklisted(normalized):
            return True
        self._config.reload_if_changed()
        contract = self._config.get_ticker_map().get(normalized)
        if contract and contract.symbol == "SKIP":
            return True
        return False

    def resolve(self, ticker: str) -> Optional[IBKRContract]:
        """
        Returns the IBKRContract for a ticker symbol, or None if unmapped.
        """
        if self.is_ignored(ticker):
            return None

        normalized = ticker.strip().upper()
        contract = self._config.get_ticker_map().get(normalized)
        if contract is None or contract.symbol == "SKIP":
            logger.info("Ticker %s not mapped — returning None to trigger Auto-Discovery", normalized)
            return None

        return contract
