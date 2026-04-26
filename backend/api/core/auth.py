"""
backend/api/core/auth.py
=========================
JWT authentication + role-based authorization.

Strategy:
  The frontend uses Supabase Auth (Next.js). Supabase now signs JWTs with
  ES256 (asymmetric). We verify using Supabase's JWKS endpoint so we never
  need to hard-code or rotate a shared secret.

  JWKS URI: {SUPABASE_URL}/auth/v1/.well-known/jwks.json

Roles:
  - anon       (no token)     → public read-only access
  - user       (valid JWT)    → higher rate limits, personal match history
  - admin      (JWT + admin)  → approve/reject lenders, view audit log,
                                view pipeline metrics

Admin detection:
  Supabase stores custom claims in the JWT's `app_metadata` field.
  To make a user admin:
    UPDATE auth.users
    SET raw_app_meta_data = raw_app_meta_data || '{"role": "admin"}'::jsonb
    WHERE email = 'admin@yourcompany.com';

Usage:

    from core.auth import get_current_user, require_admin, OptionalUser

    # Require any authenticated user
    @router.get("/my-history")
    async def history(user: dict = Depends(get_current_user)):
        ...

    # Require admin role
    @router.post("/admin/approve/{id}")
    async def approve(user: dict = Depends(require_admin)):
        ...

    # Optional auth (for rate-limit differentiation)
    @router.get("/search")
    async def search(user: OptionalUser = Depends(get_optional_user)):
        ...
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Optional

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import Request  # noqa: F811 — explicit re-import for clarity

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# Module-level JWKS client — caches public keys in memory.
# PyJWKClient fetches from Supabase's JWKS endpoint on first use, then caches.
_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        if not supabase_url:
            raise RuntimeError("SUPABASE_URL is not set")
        jwks_uri = f"{supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
        logger.info("JWKS client initialised: %s", jwks_uri)
    return _jwks_client


def _decode_token(token: str) -> dict:
    """
    Decode and verify a Supabase JWT.
    Strategy:
      1. Try JWKS (ES256) — used by Supabase Auth user tokens.
      2. Fallback to HS256 with SUPABASE_JWT_SECRET — used by anon/service-role
         tokens and locally minted test tokens.
    Raises HTTPException(401) on any verification failure.
    """
    decode_opts = {"require": ["sub", "exp"], "verify_aud": False}

    # ── Strategy 1: JWKS (ES256) ──────────────────────────────
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=["ES256", "HS256"], options=decode_opts)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    except Exception:
        pass  # fall through to HS256 fallback

    # ── Strategy 2: HS256 with SUPABASE_JWT_SECRET ────────────
    hs_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if hs_secret:
        try:
            return jwt.decode(token, hs_secret, algorithms=["HS256"], options=decode_opts)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
        except Exception as exc:
            logger.error("HS256 fallback verify error: %s", exc)

    raise HTTPException(status_code=503, detail="Authentication service unavailable")


async def get_current_user(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Security(_bearer),
    ] = None,
) -> dict:
    """
    Dependency: require a valid authenticated user.
    Returns the decoded JWT payload dict.
    Raises 401 if no token or token is invalid.
    Sets request.state.user_id for structured log correlation.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = _decode_token(credentials.credentials)
    request.state.user_id = payload.get("sub")
    request.state.user    = payload
    return payload


async def get_optional_user(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Security(_bearer),
    ] = None,
) -> Optional[dict]:
    """
    Dependency: return decoded user dict if token present and valid, else None.
    Never raises 401. Sets request.state.user_id when authenticated.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = _decode_token(credentials.credentials)
        request.state.user_id = payload.get("sub")
        request.state.user    = payload
        return payload
    except HTTPException:
        return None


def _is_admin(payload: dict) -> bool:
    """
    Check if a decoded JWT payload belongs to an admin user.
    Looks for role='admin' in app_metadata (set via Supabase dashboard or SQL).
    """
    app_meta = payload.get("app_metadata") or {}
    return app_meta.get("role") == "admin"


async def require_admin(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """
    Dependency: require admin role.
    Raises 403 if authenticated but not admin.
    """
    if not _is_admin(user):
        raise HTTPException(
            status_code=403,
            detail="Admin role required for this operation",
        )
    return user


# Type aliases for cleaner route signatures
AuthUser     = Annotated[dict, Depends(get_current_user)]
AdminUser    = Annotated[dict, Depends(require_admin)]
OptionalUser = Annotated[Optional[dict], Depends(get_optional_user)]
