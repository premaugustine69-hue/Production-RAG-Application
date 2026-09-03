"""Business logic for authentication — login, token refresh, logout, API key management.

All DB operations are async; the service is NOT a DI singleton because it needs
a fresh session per request.  Instantiate it via the FastAPI dependency functions
at the bottom of this file.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, field_validator

from production_rag.components.cache.session_cache import CachedSession, SessionCache
from production_rag.components.database.models import ApiKey, RefreshToken, User
from production_rag.components.database.repository import (
    ApiKeyRepository,
    AuditLogRepository,
    OrganizationRepository,
    RefreshTokenRepository,
    UserRepository,
)
from production_rag.server.auth.jwt_handler import JWTHandler
from production_rag.server.auth.rbac import Role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password hashing (bcrypt via passlib)
# ---------------------------------------------------------------------------

try:
    from passlib.context import CryptContext  # type: ignore[import]

    _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def hash_password(plain: str) -> str:
        return _pwd_context.hash(plain)

    def verify_password(plain: str, hashed: str) -> bool:
        return _pwd_context.verify(plain, hashed)

except ImportError:
    logger.error(
        "passlib not installed. Password hashing unavailable. "
        "Install with: pip install passlib[bcrypt]"
    )

    def hash_password(plain: str) -> str:  # type: ignore[misc]
        raise RuntimeError("passlib[bcrypt] is required for password hashing.")

    def verify_password(plain: str, hashed: str) -> bool:  # type: ignore[misc]
        raise RuntimeError("passlib[bcrypt] is required for password verification.")


# ---------------------------------------------------------------------------
# API key generation
# ---------------------------------------------------------------------------

_KEY_PREFIX_LEN = 8
_KEY_BODY_LEN = 32


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256_hash)."""
    prefix = secrets.token_urlsafe(_KEY_PREFIX_LEN)[:_KEY_PREFIX_LEN]
    body = secrets.token_urlsafe(_KEY_BODY_LEN)
    full_key = f"{prefix}.{body}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, prefix, key_hash


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    organization_slug: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit.")
        return v


class CreateApiKeyRequest(BaseModel):
    name: str
    role: str = Role.USER
    scopes: Optional[list[str]] = None
    expires_days: Optional[int] = None


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # shown ONCE at creation
    prefix: str
    role: str
    created_at: str


# ---------------------------------------------------------------------------
# Auth service
# ---------------------------------------------------------------------------

_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_MINUTES = 30


class AuthService:
    """Handles login, registration, token rotation and API key management."""

    def __init__(
        self,
        user_repo: UserRepository,
        org_repo: OrganizationRepository,
        refresh_repo: RefreshTokenRepository,
        api_key_repo: ApiKeyRepository,
        audit_repo: AuditLogRepository,
        jwt_handler: JWTHandler,
        session_cache: SessionCache,
    ) -> None:
        self._users = user_repo
        self._orgs = org_repo
        self._refresh = refresh_repo
        self._api_keys = api_key_repo
        self._audit = audit_repo
        self._jwt = jwt_handler
        self._sessions = session_cache

    # ------------------------------------------------------------------ login

    async def login(
        self,
        request: LoginRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        user = await self._users.get_active_by_email(request.email)
        if user is None:
            # Constant-time comparison to prevent user enumeration
            verify_password("dummy", "$2b$12$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Account lockout check
        if user.locked_until and user.locked_until > datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Account locked until {user.locked_until.isoformat()}. "
                       "Contact your administrator.",
            )

        if not verify_password(request.password, user.hashed_password):
            await self._users.increment_failed_attempts(user.id)
            if user.failed_login_attempts + 1 >= _MAX_FAILED_ATTEMPTS:
                from datetime import timedelta
                lockout_until = datetime.now(timezone.utc) + timedelta(
                    minutes=_LOCKOUT_MINUTES
                )
                from sqlalchemy import update
                # Directly update via flush-safe approach
                user.locked_until = lockout_until
                user.failed_login_attempts = _MAX_FAILED_ATTEMPTS
            await self._audit.log(
                "login",
                user_id=user.id,
                organization_id=user.organization_id,
                ip_address=ip_address,
                user_agent=user_agent,
                status="failure",
                details={"reason": "invalid_password"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        await self._users.reset_failed_attempts(user.id)

        tokens = await self._issue_tokens(user, ip_address, user_agent)
        await self._audit.log(
            "login",
            user_id=user.id,
            organization_id=user.organization_id,
            ip_address=ip_address,
            user_agent=user_agent,
            status="success",
        )
        return tokens

    # ---------------------------------------------------------------- refresh

    async def refresh(self, raw_refresh_token: str) -> TokenResponse:
        record = await self._refresh.get_by_token(raw_refresh_token)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )
        if record.expires_at < datetime.now(timezone.utc):
            await self._refresh.revoke(record.id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has expired",
            )

        # Rotate: revoke old token before issuing new
        await self._refresh.revoke(record.id)

        user = await self._users.get(record.user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
            )

        return await self._issue_tokens(user)

    # ----------------------------------------------------------------- logout

    async def logout(
        self,
        user_id: uuid.UUID,
        session_id: Optional[str] = None,
    ) -> None:
        await self._refresh.revoke_all_for_user(user_id)
        if session_id:
            await self._sessions.invalidate(session_id)
        await self._audit.log("logout", user_id=user_id, status="success")

    # ------------------------------------------------------------ register

    async def register(self, request: RegisterRequest) -> User:
        org = await self._orgs.get_by_slug(request.organization_slug)
        if org is None or not org.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization '{request.organization_slug}' not found.",
            )

        existing = await self._users.get_by_email(request.email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered.",
            )

        new_user = User(
            organization_id=org.id,
            email=request.email.lower(),
            hashed_password=hash_password(request.password),
            full_name=request.full_name,
            role=Role.USER,
        )
        created = await self._users.add(new_user)
        await self._audit.log(
            "create",
            user_id=created.id,
            organization_id=org.id,
            resource_type="user",
            resource_id=str(created.id),
            status="success",
        )
        return created

    # ------------------------------------------------------- API key management

    async def create_api_key(
        self,
        request: CreateApiKeyRequest,
        requesting_user: User,
    ) -> ApiKeyResponse:
        full_key, prefix, key_hash = generate_api_key()
        expires_at = None
        if request.expires_days is not None:
            from datetime import timedelta
            expires_at = datetime.now(timezone.utc) + timedelta(days=request.expires_days)

        new_key = ApiKey(
            organization_id=requesting_user.organization_id,
            user_id=requesting_user.id,
            name=request.name,
            key_prefix=prefix,
            key_hash=key_hash,
            role=request.role,
            scopes=request.scopes,
            expires_at=expires_at,
        )
        saved = await self._api_keys.add(new_key)
        await self._audit.log(
            "create",
            user_id=requesting_user.id,
            organization_id=requesting_user.organization_id,
            resource_type="api_key",
            resource_id=str(saved.id),
            status="success",
        )
        return ApiKeyResponse(
            id=str(saved.id),
            name=saved.name,
            key=full_key,  # shown ONCE
            prefix=prefix,
            role=saved.role,
            created_at=saved.created_at.isoformat(),
        )

    # ---------------------------------------------------------------- helpers

    async def _issue_tokens(
        self,
        user: User,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        access_token = self._jwt.create_access_token(
            user_id=str(user.id),
            organization_id=str(user.organization_id),
            role=user.role,
            email=user.email,
        )
        raw_refresh, refresh_hash = self._jwt.create_refresh_token()
        expires_at = self._jwt.refresh_token_expires_at()

        refresh_record = RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await self._refresh.add(refresh_record)

        # Cache session for fast validation
        cached = CachedSession(
            user_id=str(user.id),
            organization_id=str(user.organization_id),
            role=user.role,
            email=user.email,
        )
        await self._sessions.store(cached)

        return TokenResponse(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=15 * 60,  # 15 min default
        )
