"""FastAPI dependency for resolving the current authenticated principal.

This module provides ``get_current_principal`` — a unified FastAPI dependency
that supports three auth methods (in priority order):

1. **Bearer JWT** (``Authorization: Bearer <token>``)
2. **API Key** (``X-API-Key: <key>``)
3. **Legacy HTTP Basic secret** (backward-compatible with existing ``AuthSettings``)

When no ``postgres`` or ``redis`` config is present, the system falls back to
the legacy simple-secret auth so the existing RAG pipeline remains functional.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from production_rag.settings.settings import settings as get_settings

logger = logging.getLogger(__name__)

# Reusable bearer extractor — auto_error=False so we can fall through to API key
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Principal dataclass — represents the authenticated caller
# ---------------------------------------------------------------------------


@dataclass
class Principal:
    """Authenticated caller context attached to every request."""

    user_id: Optional[str]
    organization_id: Optional[str]
    role: str
    email: Optional[str]
    auth_method: str  # "jwt" | "api_key" | "legacy" | "anonymous"
    raw_token: Optional[str] = None


# Sentinel for unauthenticated-but-allowed requests (auth disabled mode)
_ANONYMOUS = Principal(
    user_id=None,
    organization_id=None,
    role="user",
    email=None,
    auth_method="anonymous",
)


# ---------------------------------------------------------------------------
# JWT resolution (lazy import to avoid hard dependency when auth disabled)
# ---------------------------------------------------------------------------


def _resolve_jwt(token: str) -> Optional[Principal]:
    """Return Principal from a valid JWT or None on failure."""
    try:
        from production_rag.server.auth.jwt_handler import create_jwt_handler

        handler = create_jwt_handler(get_settings())
        payload = handler.decode_access_token(token)
        return Principal(
            user_id=payload.get("sub"),
            organization_id=payload.get("org"),
            role=payload.get("role", "user"),
            email=payload.get("email"),
            auth_method="jwt",
            raw_token=token,
        )
    except Exception as exc:
        logger.debug("JWT decode failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# API Key resolution
# ---------------------------------------------------------------------------


async def _resolve_api_key(raw_key: str, request: Request) -> Optional[Principal]:
    """Validate an API key against the database."""
    if "." not in raw_key:
        return None
    prefix = raw_key.split(".")[0]
    try:
        from production_rag.components.database.database_component import DatabaseComponent

        db: DatabaseComponent = request.state.injector.get(DatabaseComponent)
        if not db.is_enabled:
            return None

        from production_rag.components.database.repository import ApiKeyRepository
        from datetime import datetime, timezone

        async with db.get_session() as session:
            repo = ApiKeyRepository(session)
            key_record = await repo.get_by_prefix_and_hash(prefix, raw_key)
            if key_record is None:
                return None
            if key_record.expires_at and key_record.expires_at < datetime.now(timezone.utc):
                return None
            # Touch last_used in background (best-effort)
            await repo.touch_last_used(key_record.id)
            return Principal(
                user_id=str(key_record.user_id) if key_record.user_id else None,
                organization_id=str(key_record.organization_id),
                role=key_record.role,
                email=None,
                auth_method="api_key",
            )
    except Exception as exc:
        logger.warning("API key resolution failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Legacy secret auth (backward-compatible)
# ---------------------------------------------------------------------------


def _resolve_legacy(authorization: str) -> Optional[Principal]:
    """Validate against the simple shared-secret (original behavior)."""
    import secrets as _secrets

    cfg = get_settings().server.auth
    if not cfg.enabled:
        return _ANONYMOUS
    if _secrets.compare_digest(authorization, cfg.secret):
        return Principal(
            user_id=None,
            organization_id=None,
            role="user",
            email=None,
            auth_method="legacy",
        )
    return None


# ---------------------------------------------------------------------------
# Unified dependency
# ---------------------------------------------------------------------------


async def get_current_principal(
    request: Request,
    bearer: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_bearer_scheme)
    ] = None,
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
    authorization: Annotated[str, Header()] = "",
) -> Principal:
    """FastAPI dependency — resolves caller identity from request headers.

    Priority: JWT Bearer → X-API-Key → Legacy secret → Deny.
    """
    cfg = get_settings().server.auth

    # 1. Try JWT Bearer
    if bearer is not None:
        principal = _resolve_jwt(bearer.credentials)
        if principal is not None:
            return principal
        # Token present but invalid — reject immediately
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Try API key
    if x_api_key:
        principal = await _resolve_api_key(x_api_key, request)
        if principal is not None:
            return principal
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # 3. Legacy secret / no-auth fallback
    if not cfg.enabled:
        return _ANONYMOUS

    principal = _resolve_legacy(authorization)
    if principal is not None:
        return principal

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": 'Basic realm="API", charset="UTF-8"'},
    )


# ---------------------------------------------------------------------------
# Convenience: backward-compatible ``authenticated`` replacement
# ---------------------------------------------------------------------------


async def authenticated(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> bool:
    """Drop-in replacement for the existing ``authenticated`` dependency.

    Returns True (or raises 401) — identical contract to the legacy function.
    This preserves full backward compatibility with all existing routers.
    """
    return True
