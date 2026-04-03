"""
backend/api/middleware/logging.py
===================================
Structured JSON request/response logging + Sentry integration.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger("api.request")

_SCRUBBED_HEADERS: frozenset[str] = frozenset({
    "authorization", "cookie", "set-cookie",
    "x-api-key", "x-session-token", "x-supabase-api-key",
    "proxy-authorization",
})

# Paths that produce excessive noise if logged at INFO level
_HEALTH_PATHS = frozenset({"/health", "/schema-version", "/favicon.ico"})


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, app_name: str = "lender-platform"):
        super().__init__(app)
        self._app_name       = app_name
        self._sentry_available = self._check_sentry()

    @staticmethod
    def _check_sentry() -> bool:
        try:
            import sentry_sdk
            return sentry_sdk.Hub.current.client is not None
        except ImportError:
            return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        start_ns = time.perf_counter_ns()
        response: Response = await call_next(request)
        duration_ms = (time.perf_counter_ns() - start_ns) / 1_000_000

        user_id = getattr(request.state, "user_id", None)

        log_record = {
            "ts":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "app":         self._app_name,
            "request_id":  request_id,
            "method":      request.method,
            "path":        request.url.path,
            "query":       str(request.url.query) or None,
            "status":      response.status_code,
            "duration_ms": round(duration_ms, 1),
            "user_id":     user_id,
            "client_ip":   self._get_client_ip(request),
            "user_agent":  request.headers.get("user-agent", "")[:120],
        }

        # Suppress noisy health check logs at INFO, emit at DEBUG
        is_health = request.url.path in _HEALTH_PATHS

        try:
            from core.metrics import metrics as _m
            _m.inc("requests.total")
            if response.status_code >= 500:
                _m.inc("requests.5xx")
            elif response.status_code >= 400:
                _m.inc("requests.4xx")
        except Exception:
            pass

        if response.status_code >= 500:
            logger.error(json.dumps(log_record))
            self._capture_sentry(request, response, request_id)
        elif response.status_code >= 400:
            logger.warning(json.dumps(log_record))
        elif is_health:
            logger.debug(json.dumps(log_record))
        else:
            logger.info(json.dumps(log_record))

        response.headers["X-Request-ID"] = request_id
        return response

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _safe_headers(self, request: Request) -> dict:
        return {k: v for k, v in request.headers.items() if k.lower() not in _SCRUBBED_HEADERS}

    def _capture_sentry(self, request: Request, response: Response, request_id: str) -> None:
        if not self._sentry_available:
            return
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("request_id", request_id)
                scope.set_tag("http.status_code", response.status_code)
                scope.set_context("request", {
                    "method":  request.method,
                    "url":     str(request.url),
                    "headers": self._safe_headers(request),
                })
                sentry_sdk.capture_message(
                    f"{request.method} {request.url.path} → {response.status_code}",
                    level="error",
                )
        except Exception:
            pass
