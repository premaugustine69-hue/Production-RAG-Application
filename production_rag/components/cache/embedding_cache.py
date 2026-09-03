"""Embedding vector cache backed by Redis.

Avoids re-embedding identical text chunks — significant cost and latency saving
when the same document paragraphs appear across multiple uploads.

Cache key: ``emb:{model_id}:{sha256(text)}``
TTL: 7 days by default (configurable).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from production_rag.components.cache.redis_component import RedisComponent

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 60 * 60 * 24 * 7  # 7 days in seconds
_KEY_PREFIX = "emb"


def _cache_key(model_id: str, text: str) -> str:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}:{model_id}:{text_hash}"


class EmbeddingCache:
    """Redis-backed embedding vector cache.

    If Redis is disabled this class is a transparent no-op — the embedding
    model will be called every time.
    """

    def __init__(self, redis: RedisComponent, ttl: int = _DEFAULT_TTL) -> None:
        self._redis = redis
        self._ttl = ttl

    async def get(self, model_id: str, text: str) -> Optional[list[float]]:
        """Return cached embedding or None on cache miss."""
        if not self._redis.is_enabled:
            return None
        key = _cache_key(model_id, text)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            vector: list[float] = json.loads(raw)
            logger.debug("Embedding cache HIT key=%s", key)
            return vector
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Embedding cache deserialisation failed: %s", exc)
            return None

    async def set(self, model_id: str, text: str, vector: list[float]) -> None:
        """Store embedding vector in Redis."""
        if not self._redis.is_enabled:
            return
        key = _cache_key(model_id, text)
        try:
            serialized = json.dumps(vector)
            await self._redis.set(key, serialized, ex=self._ttl)
            logger.debug("Embedding cache SET key=%s", key)
        except Exception as exc:
            logger.warning("Embedding cache SET failed: %s", exc)

    async def invalidate(self, model_id: str, text: str) -> None:
        """Explicitly remove a cached embedding."""
        key = _cache_key(model_id, text)
        await self._redis.delete(key)
