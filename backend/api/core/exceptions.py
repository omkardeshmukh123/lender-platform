from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def _error_body(
    *,
    code: str,
    message: str,
    request_id: str,
    details: Any = None,
) -> dict:
    body: dict = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        if exc.status_code >= 500:
            logger.error(
                "HTTP %d | %s %s | %s | request_id=%s",
                exc.status_code, request.method, request.url.path, exc.detail, request_id,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code=f"HTTP_{exc.status_code}",
                message=str(exc.detail),
                request_id=request_id,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        errors = []
        for e in exc.errors():
            errors.append({
                "field":   " → ".join(str(loc) for loc in e["loc"]),
                "message": e["msg"],
                "type":    e["type"],
            })
        logger.warning(
            "Validation error | %s %s | %d field(s) | request_id=%s",
            request.method, request.url.path, len(errors), request_id,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body(
                code="VALIDATION_ERROR",
                message="Request validation failed",
                request_id=request_id,
                details=errors,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        tb = traceback.format_exc()
        logger.error(
            "Unhandled exception | %s %s | %s: %s | request_id=%s\n%s",
            request.method, request.url.path,
            type(exc).__name__, exc, request_id, tb,
        )
        try:
            from core.metrics import metrics
            metrics.inc("requests.unhandled_exception")
        except Exception:
            pass
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred. Our team has been notified.",
                request_id=request_id,
            ),
        )
