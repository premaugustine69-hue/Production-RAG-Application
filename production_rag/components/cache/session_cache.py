"""Redis-backed session and conversation cache.

Provides two caches:
- **SessionCache**: Stores JWT/session identity data for O(1) lookup
  without hitting the database on every request.
- **ConversationCache**: Short-lived in-memory conversation history used
  by the RAG pipeline before messages are persisted to PostgreSQL.

Key formats:
  ``sess:{session_id}``        → serialised SessionData JSON
  ``conv:{session_id}``        → serialised list[ChatMessage] JSON
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from production_rag.components.cache.redis_component import RedisComponent

logger = logging.getLogger(__name__)

_SESSION_TTL = 60 * 60 * 8       # 8 hours
_CONVERSATION_TTL = 60 * 60 * 2  # 2 hours


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class CachedSession:
    """Lightweight session identity stored in Redis."""

    user_id: str
    organization_id: str
    role: str
    email: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class CachedMessage:
    """Individual message stored in conversation cache."""

    role: str         # "user" | "assistant" | "system"
    content: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Session Cache
# ---------------------------------------------------------------------------


class SessionCache:
    """Stores and retrieves user session data in Redis."""

    def __init__(self, redis: RedisComponent, ttl: int = _SESSION_TTL) -> None:
        self._redis = redis
        self._ttl = ttl

    @staticmethod
    def _key(session_id: str) -> str:
        return f"sess:{session_id}"

    async def store(self, session: CachedSession) -> None:
        key = self._key(session.session_id)
        await self._redis.set_json(key, asdict(session), ex=self._ttl)
        logger.debug("Session cached: session_id=%s", session.session_id)

    async def get(self, session_id: str) -> Optional[CachedSession]:
        key = self._key(session_id)
        data: Optional[Any] = await self._redis.get_json(key)
        if data is None:
            return None
        try:
            return CachedSession(**data)
        except (TypeError, KeyError) as exc:
            logger.warning("Session cache deserialisation error: %s", exc)
            return None

    async def invalidate(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))
        logger.debug("Session invalidated: session_id=%s", session_id)

    async def refresh_ttl(self, session_id: str) -> None:
        await self._redis.expire(self._key(session_id), self._ttl)


# ---------------------------------------------------------------------------
# Conversation Cache
# ---------------------------------------------------------------------------


class ConversationCache:
    """Stores recent conversation messages in Redis for context continuity."""

    def __init__(
        self, redis: RedisComponent, ttl: int = _CONVERSATION_TTL, max_messages: int = 20
    ) -> None:
        self._redis = redis
        self._ttl = ttl
        self._max_messages = max_messages

    @staticmethod
    def _key(session_id: str) -> str:
        return f"conv:{session_id}"

    async def append(self, session_id: str, role: str, content: str) -> None:
        """Append a message and keep only the last ``max_messages``."""
        key = self._key(session_id)
        raw: Optional[Any] = await self._redis.get_json(key)
        messages: list[dict] = raw if isinstance(raw, list) else []
        messages.append(asdict(CachedMessage(role=role, content=content)))
        # Keep only the tail
        if len(messages) > self._max_messages:
            messages = messages[-self._max_messages :]
        await self._redis.set_json(key, messages, ex=self._ttl)

    async def get_history(self, session_id: str) -> list[CachedMessage]:
        """Return the cached conversation history (oldest-first)."""
        key = self._key(session_id)
        raw: Optional[Any] = await self._redis.get_json(key)
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw:
            try:
                result.append(CachedMessage(**item))
            except (TypeError, KeyError):
                continue
        return result

    async def clear(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))
