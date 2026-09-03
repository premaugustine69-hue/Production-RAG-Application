"""Auth REST API — login, refresh, logout, registration, API key management.

All endpoints are under ``/v1/auth/`` to match the existing API version prefix.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from production_rag.server.auth.auth_service import (
    AuthService,
    CreateApiKeyRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from production_rag.server.auth.dependencies import Principal, get_current_principal
from production_rag.server.auth.rbac import Permission, require_role

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _get_ua(request: Request) -> str | None:
    return request.headers.get("User-Agent")


async def _build_auth_service(request: Request) -> AuthService:
    """Construct AuthService from DI container — requires postgres + redis."""
    from production_rag.components.cache.redis_component import RedisComponent
    from production_rag.components.cache.session_cache import SessionCache
    from production_rag.components.database.database_component import DatabaseComponent
    from production_rag.components.database.repository import (
        ApiKeyRepository,
        AuditLogRepository,
        OrganizationRepository,
        RefreshTokenRepository,
        UserRepository,
    )
    from production_rag.server.auth.jwt_handler import create_jwt_handler
    from production_rag.settings.settings import settings

    db: DatabaseComponent = request.state.injector.get(DatabaseComponent)
    if not db.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured — auth endpoints require PostgreSQL.",
        )

    redis: RedisComponent = request.state.injector.get(RedisComponent)
    session_cache = SessionCache(redis)
    jwt_handler = create_jwt_handler(settings())

    async with db.get_session() as session:
        return AuthService(
            user_repo=UserRepository(session),
            org_repo=OrganizationRepository(session),
            refresh_repo=RefreshTokenRepository(session),
            api_key_repo=ApiKeyRepository(session),
            audit_repo=AuditLogRepository(session),
            jwt_handler=jwt_handler,
            session_cache=session_cache,
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@auth_router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email and password",
)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """Issue JWT access + refresh tokens for valid credentials."""
    svc = await _build_auth_service(request)
    return await svc.login(
        body, ip_address=_get_ip(request), user_agent=_get_ua(request)
    )


@auth_router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue new access token",
)
async def refresh_token(body: RefreshRequest, request: Request) -> TokenResponse:
    """Exchange a valid refresh token for a new access + refresh token pair."""
    svc = await _build_auth_service(request)
    return await svc.refresh(body.refresh_token)


from fastapi import Response

@auth_router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Revoke all refresh tokens for the current user",
)
async def logout(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    """Revoke all refresh tokens and invalidate the session cache."""
    if principal.user_id is None:
        return  # Anonymous / legacy auth — nothing to revoke
    import uuid

    svc = await _build_auth_service(request)
    await svc.logout(uuid.UUID(principal.user_id))


@auth_router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Self-register a new user account",
)
async def register(body: RegisterRequest, request: Request) -> dict:
    """Register a new user under an existing organisation."""
    svc = await _build_auth_service(request)
    user = await svc.register(body)
    return {"id": str(user.id), "email": user.email, "role": user.role}


@auth_router.post(
    "/api-keys",
    summary="Create a new API key for programmatic access",
)
async def create_api_key(
    body: CreateApiKeyRequest,
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    """Create a service API key. Returned key is shown only once."""
    require_role("user", principal.role)
    if principal.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key creation requires a JWT-authenticated user.",
        )
    import uuid
    from production_rag.components.database.database_component import DatabaseComponent
    from production_rag.components.database.repository import UserRepository

    db: DatabaseComponent = request.state.injector.get(DatabaseComponent)
    async with db.get_session() as session:
        user = await UserRepository(session).get(uuid.UUID(principal.user_id))
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    svc = await _build_auth_service(request)
    api_key_response = await svc.create_api_key(body, user)
    return api_key_response.model_dump()


@auth_router.get(
    "/me",
    summary="Return current principal identity",
)
async def whoami(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    """Return the identity of the authenticated caller."""
    return {
        "user_id": principal.user_id,
        "organization_id": principal.organization_id,
        "role": principal.role,
        "email": principal.email,
        "auth_method": principal.auth_method,
    }
