import json
import logging
import time
from typing import Any, Optional
import redis
from app.core.config import settings

logger = logging.getLogger(__name__)


class CacheClient:
    def __init__(self, url: str, ttl: int = 300):
        self.ttl = ttl
        self._redis: Optional[redis.Redis] = None
        self._in_memory: dict[str, tuple[str, float]] = {}
        try:
            self._redis = redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2)
            # Test connectivity
            self._redis.ping()
            self._is_redis_available = True
            logger.info("Connected to Redis cache at %s", url)
        except Exception as exc:
            self._is_redis_available = False
            logger.info("Redis not available (%s); using in-memory cache fallback", exc)

    @property
    def is_available(self) -> bool:
        return self._is_redis_available

    def get(self, key: str) -> Optional[Any]:
        if self._is_redis_available and self._redis:
            try:
                data = self._redis.get(key)
                if data:
                    return json.loads(data)
            except Exception as exc:
                logger.warning("Redis GET failed for %s: %s", key, exc)

        # In-memory fallback
        if key in self._in_memory:
            val, expiry = self._in_memory[key]
            if time.time() < expiry:
                return json.loads(val)
            del self._in_memory[key]
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self.ttl
        serialized = json.dumps(value, default=str)
        if self._is_redis_available and self._redis:
            try:
                self._redis.set(key, serialized, ex=ttl)
                return
            except Exception as exc:
                logger.warning("Redis SET failed for %s: %s", key, exc)

        # In-memory fallback
        self._in_memory[key] = (serialized, time.time() + ttl)

    def delete(self, key: str) -> None:
        if self._is_redis_available and self._redis:
            try:
                self._redis.delete(key)
            except Exception as exc:
                logger.warning("Redis DELETE failed for %s: %s", key, exc)

        self._in_memory.pop(key, None)

    def delete_pattern(self, pattern: str) -> None:
        if self._is_redis_available and self._redis:
            try:
                keys = self._redis.keys(pattern)
                if keys:
                    self._redis.delete(*keys)
            except Exception as exc:
                logger.warning("Redis DELETE pattern failed for %s: %s", pattern, exc)

        # In-memory fallback
        prefix = pattern.replace("*", "")
        to_del = [k for k in self._in_memory if k.startswith(prefix)]
        for k in to_del:
            self._in_memory.pop(k, None)


cache = CacheClient(settings.redis_url, settings.cache_ttl_seconds)
