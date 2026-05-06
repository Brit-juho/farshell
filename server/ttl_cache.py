"""TTL 캐시 일반화 유틸 (Phase 8 G6).

purplemux session-meta-cache.ts 패턴을 단순화한 thread-safe TTL 캐시.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Thread-safe TTL 캐시.

    사용:
        cache = TTLCache[int](ttl=5.0)
        val = cache.get_or_fetch("key", lambda: expensive_call())
    """

    def __init__(self, ttl: float = 5.0):
        self._ttl = ttl
        self._store: dict[str, tuple[float, T]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[T]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)

    def get_or_fetch(self, key: str, fetcher: Callable[[], T]) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = fetcher()
        self.set(key, value)
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
