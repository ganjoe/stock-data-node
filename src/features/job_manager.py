import threading
import logging
from typing import Callable

logger = logging.getLogger(__name__)

class JobManager:
    """
    Manages the state of the feature calculation job to prevent overlapping executions.
    Requirement F-SYS-030: Job Overlap Protection.
    """
    _instance = None
    _lock = threading.Lock()

    _is_running: bool
    _internal_lock: threading.Lock

    def __new__(cls):
        """Singleton pattern to ensure only one JobManager tracks the state."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(JobManager, cls).__new__(cls)
                cls._instance._is_running = False
                cls._instance._internal_lock = threading.Lock()
            return cls._instance

    def start_feature_calculation(self, run_func: Callable, *args, **kwargs) -> bool:
        """
        Attempts to start the feature calculation job in a background thread.
        Returns True if started successfully, False if already running.
        """
        with self._internal_lock:
            if self._is_running:
                logger.warning("Feature calculation trigger ignored: A process is already running.")
                return False
            self._is_running = True

        # Run in background thread so API doesn't block
        thread = threading.Thread(target=self._run_wrapper, args=(run_func, args, kwargs))
        thread.daemon = True
        thread.start()
        
        logger.info("Feature calculation job started in background.")
        return True

    def _run_wrapper(self, run_func, args, kwargs):
        try:
            logger.info("Background job execution started.")
            run_func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Background job failed with exception: {e}")
        finally:
            with self._internal_lock:
                self._is_running = False
                logger.info("Background job execution finished. System ready for re-trigger.")

    @property
    def is_running(self) -> bool:
        with self._internal_lock:
            return self._is_running
