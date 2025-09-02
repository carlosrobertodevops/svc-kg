# src/cache.py
import os, json, time, hashlib, asyncio
from typing import Any, Optional
from collections import OrderedDict

class _Entry:
    def __init__(self, value: Any, exp: float):
        self.value = value
        self.exp = exp

class TTLCache:
    def __init__(self, ttl: int = 60, max_items: int = 512):
        self.ttl = ttl
        self.max_items = max_items
        self._data: "OrderedDict[str, _Entry]" = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def _prune(self) -> None:
        now = self._now()
        expired = [k for k, e in self._data.items() if e.exp <= now]
        for k in expired:
            self._data.pop(k, None)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)

    async def get(self, key: str) -> Optional[Any]:
        e = self._data.get(key)
        if e and e.exp > self._now():
            self._data.move_to_end(key)
            return e.value
        if e:
            self._data.pop(key, None)
        return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        self._prune()
        exp = self._now() + (ttl if ttl is not None else self.ttl)
        self._data[key] = _Entry(value, exp)
        self._data.move_to_end(key)

    def key_for(self, function: str, payload: dict) -> str:
        s = json.dumps([function, payload], sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(s.encode()).hexdigest()

    async def acquire_key_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))
CACHE_MAX_ITEMS = int(os.getenv("CACHE_MAX_ITEMS", "512"))
cache = TTLCache(ttl=CACHE_TTL_SECONDS, max_items=CACHE_MAX_ITEMS)
