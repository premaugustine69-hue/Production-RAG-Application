"""Generic async repository base + concrete repositories for every ORM model."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Generic, Optional, Sequence, Type, TypeVar

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from production_rag.components.database.models import (
    ApiKey,
    AuditLog,
    Base,
    ChatMessage,
    ChatSession,
    DocumentMetadata,
    Organization,
    PlatformConfig,
    RefreshToken,
    User,
    UserSession,
)

ModelT = TypeVar("ModelT", bound=Base)


# ---------------------------------------------------------------------------
# Generic base
# ---------------------------------------------------------------------------


class BaseRepository(Generic[ModelT]):
    """Thin async repository wrapping SQLAlchemy AsyncSession."""

    def __init__(self, model: Type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def get(self, pk: uuid.UUID) -> Optional[ModelT]:
        return await self._session.get(self._model, pk)

    async def list_all(self) -> Sequence[ModelT]:
        result = await self._session.execute(select(self._model))
        return result.scalars().all()

    async def add(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self._session.delete(entity)
        await self._session.flush()


# ---------------------------------------------------------------------------
# Organization repository
# ---------------------------------------------------------------------------


class OrganizationRepository(BaseRepository[Organization]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Organization, session)

    async def get_by_slug(self, slug: str) -> Optional[Organization]:
        result = await self._session.execute(
            select(Organization).where(Organization.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_active(self, pk: uuid.UUID) -> Optional[Organization]:
        result = await self._session.execute(
            select(Organization).where(
                Organization.id == pk, Organization.is_active.is_(True)
            )
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# User repository
# ---------------------------------------------------------------------------


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self._session.execute(
            select(User).where(User.email == email.lower())
        )
        return result.scalar_one_or_none()

    async def get_active_by_email(self, email: str) -> Optional[User]:
        result = await self._session.execute(
            select(User).where(
                User.email == email.lower(), User.is_active.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def list_by_org(self, org_id: uuid.UUID) -> Sequence[User]:
        result = await self._session.execute(
            select(User).where(User.organization_id == org_id)
        )
        return result.scalars().all()

    async def increment_failed_attempts(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                failed_login_attempts=User.failed_login_attempts + 1,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.flush()

    async def reset_failed_attempts(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                failed_login_attempts=0,
                locked_until=None,
                last_login=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.flush()


# ---------------------------------------------------------------------------
# Refresh Token repository
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(RefreshToken, session)

    async def get_by_token(self, raw_token: str) -> Optional[RefreshToken]:
        token_hash = _sha256(raw_token)
        result = await self._session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def revoke(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(is_revoked=True)
        )
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.is_revoked.is_(False))
            .values(is_revoked=True)
        )
        await self._session.flush()


# ---------------------------------------------------------------------------
# API Key repository
# ---------------------------------------------------------------------------


class ApiKeyRepository(BaseRepository[ApiKey]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ApiKey, session)

    async def get_by_prefix_and_hash(
        self, prefix: str, raw_key: str
    ) -> Optional[ApiKey]:
        key_hash = _sha256(raw_key)
        result = await self._session.execute(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.key_hash == key_hash,
                ApiKey.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def touch_last_used(self, key_id: uuid.UUID) -> None:
        await self._session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await self._session.flush()

    async def list_by_org(self, org_id: uuid.UUID) -> Sequence[ApiKey]:
        result = await self._session.execute(
            select(ApiKey).where(
                ApiKey.organization_id == org_id, ApiKey.is_active.is_(True)
            )
        )
        return result.scalars().all()


# ---------------------------------------------------------------------------
# User Session repository
# ---------------------------------------------------------------------------


class UserSessionRepository(BaseRepository[UserSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(UserSession, session)

    async def get_active_by_hash(self, session_hash: str) -> Optional[UserSession]:
        result = await self._session.execute(
            select(UserSession).where(
                UserSession.session_token_hash == session_hash,
                UserSession.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def deactivate(self, session_id: uuid.UUID) -> None:
        await self._session.execute(
            update(UserSession)
            .where(UserSession.id == session_id)
            .values(is_active=False)
        )
        await self._session.flush()


# ---------------------------------------------------------------------------
# Chat repositories
# ---------------------------------------------------------------------------


class ChatSessionRepository(BaseRepository[ChatSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ChatSession, session)

    async def list_by_user(self, user_id: uuid.UUID) -> Sequence[ChatSession]:
        result = await self._session.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.is_active.is_(True))
            .order_by(ChatSession.updated_at.desc())
        )
        return result.scalars().all()


class ChatMessageRepository(BaseRepository[ChatMessage]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ChatMessage, session)

    async def list_by_session(
        self, session_id: uuid.UUID, limit: int = 100
    ) -> Sequence[ChatMessage]:
        result = await self._session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Document Metadata repository
# ---------------------------------------------------------------------------


class DocumentMetadataRepository(BaseRepository[DocumentMetadata]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DocumentMetadata, session)

    async def get_by_rag_doc_id(self, rag_doc_id: str) -> Optional[DocumentMetadata]:
        result = await self._session.execute(
            select(DocumentMetadata).where(
                DocumentMetadata.rag_doc_id == rag_doc_id
            )
        )
        return result.scalar_one_or_none()

    async def list_by_org(self, org_id: uuid.UUID) -> Sequence[DocumentMetadata]:
        result = await self._session.execute(
            select(DocumentMetadata).where(
                DocumentMetadata.organization_id == org_id
            ).order_by(DocumentMetadata.created_at.desc())
        )
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Audit Log repository
# ---------------------------------------------------------------------------


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AuditLog, session)

    async def log(
        self,
        action: str,
        *,
        user_id: Optional[uuid.UUID] = None,
        organization_id: Optional[uuid.UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        correlation_id: Optional[str] = None,
        details: Optional[dict] = None,
        status: str = "success",
    ) -> AuditLog:
        entry = AuditLog(
            action=action,
            user_id=user_id,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            correlation_id=correlation_id,
            details=details,
            status=status,
        )
        return await self.add(entry)


# ---------------------------------------------------------------------------
# Platform Config repository
# ---------------------------------------------------------------------------


class PlatformConfigRepository(BaseRepository[PlatformConfig]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(PlatformConfig, session)

    async def get_value(
        self,
        key: str,
        org_id: Optional[uuid.UUID] = None,
    ) -> Optional[dict]:
        result = await self._session.execute(
            select(PlatformConfig).where(
                PlatformConfig.config_key == key,
                PlatformConfig.organization_id == org_id,
            )
        )
        row = result.scalar_one_or_none()
        return row.config_value if row else None

    async def set_value(
        self,
        key: str,
        value: dict,
        org_id: Optional[uuid.UUID] = None,
        is_secret: bool = False,
    ) -> PlatformConfig:
        result = await self._session.execute(
            select(PlatformConfig).where(
                PlatformConfig.config_key == key,
                PlatformConfig.organization_id == org_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.config_value = value
            existing.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
            await self._session.refresh(existing)
            return existing
        new_cfg = PlatformConfig(
            config_key=key,
            config_value=value,
            organization_id=org_id,
            is_secret=is_secret,
        )
        return await self.add(new_cfg)
