import asyncpg
from fastapi import HTTPException, Request

from core.cache import RedisCache, NullCache


def get_db(request: Request) -> asyncpg.Pool:
    pool = getattr(request.app.state, "db", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return pool


def get_cache(request: Request) -> RedisCache:
    return getattr(request.app.state, "cache", NullCache())
