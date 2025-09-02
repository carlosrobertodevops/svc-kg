import time
import json
from typing import Any, Optional
from config import Settings

try:
    from redis.asyncio import from_url as redis_from_url
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False


class _MemoryCache:
    def __init__(self):
        self._store = {}

    async def start(self):  # compat
        return

    async def close(self):
        self._store.clear()

    async def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if expires_at and time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int = 60):
        expires_at = time.time() + ttl if ttl else None
        self._store[key] = (expires_at, value)


class _RedisCache:
    def __init__(self, url: str):
        self._url = url
        self._client = None

    async def start(self):
        self._client = redis_from_url(self._url, encoding="utf-8", decode_responses=True)

    async def close(self):
        if self._client:
            await self._client.close()

    async def get(self, key: str) -> Optional[Any]:
        if not self._client:
            return None
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: Any, ttl: int = 60):
        if not self._client:
            return
        await self._client.set(key, json.dumps(value, separators=(",", ":"), ensure_ascii=False), ex=ttl)


def get_cache(settings: Settings):
    if settings.enable_redis_cache and REDIS_AVAILABLE:
        return _RedisCache(settings.redis_url)
    return _MemoryCache()
