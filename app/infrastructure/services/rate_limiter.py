"""In-memory rate limiter with sliding window.

Sufficient for single-instance deployments. For multi-instance,
replace with Redis-backed implementation.
"""
import time
import threading
from typing import Dict, List


class RateLimiter:
    """Thread-safe sliding window rate limiter.

    Example:
        >>> limiter = RateLimiter()
        >>> limiter.is_allowed("ip:1.2.3.4:login", max_requests=5, window_seconds=900)
        True
    """

    def __init__(self):
        self._store: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Check if request is within rate limit.

        Args:
            key: Unique identifier (e.g., "ip:1.2.3.4:login")
            max_requests: Maximum allowed requests in the window
            window_seconds: Time window in seconds

        Returns:
            True if request is allowed, False if rate limited
        """
        now = time.time()
        window_start = now - window_seconds

        with self._lock:
            timestamps = self._store.get(key, [])
            # Remove expired timestamps
            timestamps = [t for t in timestamps if t > window_start]

            if len(timestamps) >= max_requests:
                self._store[key] = timestamps
                return False

            timestamps.append(now)
            self._store[key] = timestamps
            return True

    def cleanup(self, max_age_seconds: int = 3600) -> int:
        """Remove old entries to prevent memory growth.

        Args:
            max_age_seconds: Remove entries older than this

        Returns:
            Number of keys removed
        """
        cutoff = time.time() - max_age_seconds
        with self._lock:
            keys_to_remove = []
            for key, timestamps in self._store.items():
                if not timestamps or all(t <= cutoff for t in timestamps):
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del self._store[key]
            return len(keys_to_remove)
