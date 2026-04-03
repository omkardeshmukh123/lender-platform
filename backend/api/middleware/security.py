from __future__ import annotations

import os
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

_SECURITY_HEADERS = {
    "X-Content-Type-Options":  "nosniff",
    "X-Frame-Options":         "DENY",
    "X-XSS-Protection":        "0",
    "Referrer-Policy":         "strict-origin-when-cross-origin",
    "Permissions-Policy":      "geolocation=(), microphone=(), camera=()",
}

_HSTS = "max-age=31536000; includeSubDomains"

# Tight CSP for the API itself: only own origin, no scripts, no embeds.
# Frontend has its own CSP via next.config.js.
_CSP = (
    "default-src 'none'; "
    "frame-ancestors 'none'"
)

_ENV = os.environ.get("ENV", "production").lower()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)

        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)

        response.headers.setdefault("Content-Security-Policy", _CSP)

        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", _HSTS)

        # Prevent caching of sensitive API responses by default.
        # Individual data endpoints override with their own Cache-Control.
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"

        return response
