"""
backend/api/main.py
====================
FastAPI application — Lender Platform public API.

  GET  /v1/lenders/search
  GET  /v1/lenders/{id}
  GET  /v1/policies/filter
  GET  /v1/loans/compare
  POST /v1/loans/match
  GET/POST /v1/admin/*
  GET /health
  GET /schema-version
"""

from contextlib import asynccontextmanager
import os
import sys
import logging
from pathlib import Path

_API_DIR = Path(__file__).parent
sys.path.insert(0, str(_API_DIR))

_ENV_FILE = _API_DIR.parent.parent / ".env"
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_ENV_FILE, override=False)
except ImportError:
    # Fallback for environments without python-dotenv
    if _ENV_FILE.exists():
        with open(_ENV_FILE, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _sep, _rest = _line.partition("=")
                    _k = _k.strip(); _v = _rest.strip().strip('"').strip("'")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from limiter import limiter
from routers import lenders, policies, loans
from routers import admin as admin_router
from middleware.logging import StructuredLoggingMiddleware
from middleware.security import SecurityHeadersMiddleware
from core.cache import create_redis_cache
from core.config import cfg
from core.metrics import metrics
from core.exceptions import register_exception_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_ENV    = os.environ.get("ENV", "production").lower()
_IS_PROD = _ENV == "production"


def _init_sentry() -> None:
    dsn = os.environ.get("SENTRY_DSN", "")
    if not dsn:
        logger.info("SENTRY_DSN not set — error tracking disabled")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
        sentry_sdk.init(
            dsn=dsn,
            environment=_ENV,
            traces_sample_rate=cfg.sentry_traces_rate,
            integrations=[FastApiIntegration(), AsyncPGIntegration()],
            send_default_pii=False,
        )
        logger.info("Sentry initialized (env=%s)", _ENV)
    except ImportError:
        logger.warning("sentry-sdk not installed — error tracking disabled")
    except Exception as exc:
        logger.warning("Sentry init failed: %s", exc)


def _validate_startup_config() -> None:
    errors = []
    jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not jwt_secret:
        errors.append(
            "SUPABASE_JWT_SECRET is not set. "
            "Find it: Supabase dashboard → Project Settings → API → JWT Secret."
        )
    elif len(jwt_secret) < 32:
        errors.append(
            f"SUPABASE_JWT_SECRET too short ({len(jwt_secret)} chars, need ≥ 32)."
        )
    if not os.environ.get("DATABASE_URL"):
        errors.append("DATABASE_URL is not set.")
    supabase_url = os.environ.get("SUPABASE_URL", "")
    if not supabase_url or not supabase_url.startswith("https://"):
        errors.append("SUPABASE_URL is not set or is not HTTPS.")
    if errors:
        for e in errors:
            logger.critical("STARTUP CONFIG ERROR: %s", e)
        raise RuntimeError(
            f"Server startup aborted — {len(errors)} config error(s):\n"
            + "\n".join(f"  • {e}" for e in errors)
        )
    logger.info("Startup config validated OK")


async def _create_pool() -> asyncpg.Pool:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return await asyncpg.create_pool(
        dsn,
        min_size=cfg.db_pool_min,
        max_size=cfg.db_pool_max,
        command_timeout=cfg.db_command_timeout,
        statement_cache_size=0,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_startup_config()
    cfg.log_summary()
    _init_sentry()

    app.state.db = await _create_pool()
    logger.info("DB pool created (min=%d max=%d)", cfg.db_pool_min, cfg.db_pool_max)

    app.state.cache = await create_redis_cache()

    # Listen for policy_updated notifications from DB trigger (migration 017)
    # When a policy is approved, invalidate the loans_match cache immediately
    _notify_conn: asyncpg.Connection | None = None
    try:
        _db_url = os.environ.get("DATABASE_URL")
        if not _db_url:
            raise RuntimeError("DATABASE_URL not set — cannot start policy notification listener")
        _notify_conn = await asyncpg.connect(
            _db_url,
            statement_cache_size=0,
        )

        async def _on_policy_updated(conn, pid, channel, payload):
            try:
                import json as _json
                data = _json.loads(payload)
                logger.info(
                    "policy_updated: policy_id=%s lender_id=%s loan_type=%s action=%s — invalidating match cache",
                    data.get("policy_id"), data.get("lender_id"),
                    data.get("loan_type"), data.get("action"),
                )
                await app.state.cache.delete_pattern("lp:loans_match:*")
                await app.state.cache.delete_pattern("lp:policies_filter:*")
                metrics.inc("cache.invalidation", tags={"trigger": "policy_updated"})
            except Exception as exc:
                logger.warning("policy_updated handler error: %s", exc)

        await _notify_conn.add_listener("policy_updated", _on_policy_updated)
        logger.info("Listening for policy_updated notifications on DB channel")
    except Exception as exc:
        logger.warning("Could not set up policy_updated listener: %s — cache invalidation on policy approval disabled", exc)
        _notify_conn = None

    yield

    if _notify_conn:
        try:
            await _notify_conn.remove_listener("policy_updated", _on_policy_updated)
            await _notify_conn.close()
        except Exception:
            pass

    await app.state.db.close()
    logger.info("DB pool closed")

    if hasattr(app.state, "cache") and hasattr(app.state.cache, "_client") and app.state.cache._client:
        await app.state.cache._client.aclose()
        logger.info("Redis connection closed")


_docs_url  = None if _IS_PROD else "/docs"
_redoc_url = None if _IS_PROD else "/redoc"

app = FastAPI(
    title="Lender Platform API",
    version="1.0.0",
    description="Search, filter, and compare Indian NBFCs and banks.",
    lifespan=lifespan,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url="/openapi.json" if not _IS_PROD else None,
)

# Register centralized exception handlers before any middleware
register_exception_handlers(app)

# Middleware (LIFO — last registered runs first on request)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeadersMiddleware)

_raw_origins    = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Cache"],
)
app.add_middleware(StructuredLoggingMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_V1 = "/v1"
app.include_router(lenders.router,      prefix=f"{_V1}/lenders",  tags=["Lenders"])
app.include_router(policies.router,     prefix=f"{_V1}/policies", tags=["Policies"])
app.include_router(loans.router,        prefix=f"{_V1}/loans",    tags=["Loans"])
app.include_router(admin_router.router, prefix=f"{_V1}/admin",    tags=["Admin"])


@app.get("/health", tags=["Health"])
async def health(request: Request):
    cache_ok = await app.state.cache.ping() if hasattr(app.state, "cache") else False
    pool: asyncpg.Pool = app.state.db

    try:
        async with pool.acquire() as conn:
            pg_version = await conn.fetchval("SELECT current_setting('server_version')")
        db_ok = True
    except Exception as exc:
        pg_version = None
        db_ok = False
        logger.error("Health check DB error: %s", exc)

    m = metrics.health_summary()
    status = "ok" if db_ok else "degraded"

    body = {
        "status":      status,
        "env":         _ENV,
        "db":          {"ok": db_ok, "version": pg_version},
        "cache":       {"ok": cache_ok},
        "pool":        {
            "size":    pool.get_size(),
            "idle":    pool.get_idle_size(),
            "max":     cfg.db_pool_max,
        },
        "metrics":     m,
    }

    if not db_ok:
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/schema-version", tags=["Health"])
async def schema_version():
    try:
        async with app.state.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT version, name, applied_at FROM schema_versions "
                "ORDER BY version DESC LIMIT 1"
            )
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})
    if not row:
        return {"version": 0, "name": "none", "applied_at": None}
    return {
        "version":    row["version"],
        "name":       row["name"],
        "applied_at": row["applied_at"].isoformat() if row["applied_at"] else None,
    }
