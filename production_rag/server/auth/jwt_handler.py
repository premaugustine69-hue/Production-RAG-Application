"""JWT access-token and refresh-token handler.

- Access tokens: short-lived (default 15 min), signed HS256.
- Refresh tokens: long-lived (default 7 days), stored as SHA-256 hash in Postgres.
- Both tokens embed ``sub`` (user_id), ``org`` (organization_id), ``role``,
  and ``jti`` (JWT ID) for revocation checks.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from production_rag.settings.settings import Settings

logger = logging.getLogger(__name__)

try:
    from jose import JWTError, jwt  # type: ignore[import]

    _JOSE_AVAILABLE = True
except ImportError:
    _JOSE_AVAILABLE = False
    logger.error(
        "python-jose not installed. JWT auth will not work. "
        "Install with: pip install python-jose[cryptography]"
    )

_ALGORITHM = "HS256"


class JWTHandler:
    """Creates and validates JWT tokens."""

    def __init__(
        self,
        secret_key: str,
        access_token_expire_minutes: int = 15,
        refresh_token_expire_days: int = 7,
    ) -> None:
        self._secret = secret_key
        self._access_expire = timedelta(minutes=access_token_expire_minutes)
        self._refresh_expire = timedelta(days=refresh_token_expire_days)

    # ------------------------------------------------------------------ create

    def create_access_token(
        self,
        user_id: str,
        organization_id: str,
        role: str,
        email: str,
        extra_claims: Optional[dict[str, Any]] = None,
    ) -> str:
        """Return a signed JWT access token."""
        if not _JOSE_AVAILABLE:
            raise RuntimeError("python-jose is required for JWT functionality.")
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "sub": user_id,
            "org": organization_id,
            "role": role,
            "email": email,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + self._access_expire,
            "type": "access",
        }
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def create_refresh_token(self) -> tuple[str, str]:
        """Return (raw_token, sha256_hash) for a new refresh token.

        The raw token is returned to the client; only the hash is stored.
        """
        raw = secrets.token_urlsafe(48)
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        return raw, hashed

    def refresh_token_expires_at(self) -> datetime:
        return datetime.now(timezone.utc) + self._refresh_expire

    # ---------------------------------------------------------------- validate

    def decode_access_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a JWT access token.

        Raises:
            ValueError: if the token is invalid, expired, or wrong type.
        """
        if not _JOSE_AVAILABLE:
            raise RuntimeError("python-jose is required for JWT functionality.")
        try:
            payload = jwt.decode(token, self._secret, algorithms=[_ALGORITHM])
        except JWTError as exc:
            raise ValueError(f"Invalid token: {exc}") from exc

        if payload.get("type") != "access":
            raise ValueError("Token is not an access token.")
        return payload

    def decode_token_unverified(self, token: str) -> dict[str, Any]:
        """Decode without signature verification (for logging / debugging only)."""
        if not _JOSE_AVAILABLE:
            return {}
        try:
            return jwt.decode(
                token, self._secret, algorithms=[_ALGORITHM], options={"verify_signature": False}
            )
        except Exception:
            return {}


def create_jwt_handler(settings: Settings) -> JWTHandler:
    """Factory called from DI container."""
    auth_cfg = settings.server.auth
    secret = auth_cfg.secret or secrets.token_urlsafe(32)
    return JWTHandler(
        secret_key=secret,
        access_token_expire_minutes=getattr(
            auth_cfg, "access_token_expire_minutes", 15
        ),
        refresh_token_expire_days=getattr(
            auth_cfg, "refresh_token_expire_days", 7
        ),
    )
