"""
backend/api/core/cache.py
==========================
Redis-backed response cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
from enum import IntEnum
from typing import Any, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

_KEY_DIGEST_LENGTH = int(os.environ.get("CACHE_KEY_LENGTH", "32"))


class CacheTTL(IntEnum):
    MATCH  = 300
    SEARCH = 300
    DETAIL = 600
    STATS  = 300
    HEALTH = 30


def _jitter(ttl: int, fraction: float = 0.10) -> int:
    """Add ±10% random jitter to TTL to prevent thundering herd on expiry."""
    delta = int(ttl * fraction)
    return ttl + random.randint(-delta, delta)


def make_key(namespace: str, params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    digest    = hashlib.sha256(f"{namespace}:{canonical}".encode()).hexdigest()[:_KEY_DIGEST_LENGTH]
    return f"lp:{namespace}:{digest}"


class RedisCache:
    def __init__(self, client):
        self._client = client

    async def get(self, key: str) -> Optional[Any]:
        if self._client is None:
            return None
        try:
            raw = await self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.debug("Cache GET failed for key=%s: %s", key[:40], exc)
            return None

    async def set(self, key: str, value: Any, ttl: int = CacheTTL.SEARCH) -> None:
        if self._client is None:
            return
        try:
            await self._client.set(
                key,
                json.dumps(value, default=str),
                ex=_jitter(ttl),
            )
        except Exception as exc:
            logger.debug("Cache SET failed for key=%s: %s", key[:40], exc)

    async def delete(self, key: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.delete(key)
        except Exception as exc:
            logger.debug("Cache DELETE failed: %s", exc)

    async def delete_pattern(self, pattern: str) -> int:
        if self._client is None:
            return 0
        try:
            keys = []
            async for key in self._client.scan_iter(match=pattern, count=200):
                keys.append(key)
            if keys:
                deleted = await self._client.delete(*keys)
                logger.debug("Cache invalidated %d keys matching %s", deleted, pattern)
                return deleted
            return 0
        except Exception as exc:
            logger.warning("Cache DELETE pattern failed for %s: %s", pattern, exc)
            return 0

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return await self._client.ping()
        except Exception:
            return False


class NullCache(RedisCache):
    def __init__(self):
        super().__init__(None)


def get_cache(request: Request) -> RedisCache:
    return getattr(request.app.state, "cache", NullCache())


async def create_redis_cache() -> RedisCache:
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        logger.info("REDIS_URL not set — caching disabled")
        return NullCache()
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        await client.ping()
        logger.info("Redis cache connected: %s", redis_url.split("@")[-1])
        return RedisCache(client)
    except Exception as exc:
        logger.warning("Redis connection failed (%s) — caching disabled", exc)
        return NullCache()
