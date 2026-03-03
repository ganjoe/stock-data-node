"""
priority_queue.py — T-004
Thread-safe priority queue with preemption support. (F-FNC-010, F-FNC-020)
"""
from __future__ import annotations

import heapq
import logging
import threading
from typing import Optional

from models import DownloadPriority, DownloadRequest, IPriorityQueue

logger = logging.getLogger(__name__)


class DownloadQueue(IPriorityQueue):
    """
    Thread-safe min-heap priority queue.
    DownloadRequest.__lt__ ensures API (prio 1) < WATCHER (prio 2),
    and among equal priorities items are ordered by creation time (FIFO).
    """

    def __init__(self) -> None:
        self._heap: list[DownloadRequest] = []
        self._lock = threading.Lock()

    def enqueue(self, request: DownloadRequest) -> None:
        """Pushes a request onto the priority heap."""
        with self._lock:
            heapq.heappush(self._heap, request)
            logger.debug(
                "Enqueued %s/%s (prio=%s) — queue size: %d",
                request.ticker, request.timeframe,
                request.priority.name, len(self._heap)
            )

    def dequeue(self) -> Optional[DownloadRequest]:
        """Pops the highest-priority request. Returns None if queue is empty."""
        with self._lock:
            if not self._heap:
                return None
            item = heapq.heappop(self._heap)
            logger.debug(
                "Dequeued %s/%s (prio=%s) — queue size: %d",
                item.ticker, item.timeframe,
                item.priority.name, len(self._heap)
            )
            return item

    def has_higher_priority_waiting(self, current_priority: DownloadPriority) -> bool:
        """
        Returns True if there is a queued request with strictly higher priority
        (i.e., lower numeric value) than current_priority.
        Used during chunk downloads to decide whether to preempt.
        """
        with self._lock:
            if not self._heap:
                return False
            top = self._heap[0]
            return int(top.priority) < int(current_priority)

    def size(self) -> int:
        """Returns current queue depth."""
        with self._lock:
            return len(self._heap)
