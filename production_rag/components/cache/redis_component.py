"""Redis connection pool — DI-managed singleton component.

When Redis is not configured or not reachable, all cache/rate-limit operations
degrade gracefully (no-op / bypass) so the FastAPI RAG service keeps running.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from injector import inject, singleton

from production_rag.settings.settings import Settings

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis  # type: ignore[import]

    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False
    logger.warning(
        "redis[asyncio] not installed — Redis features will be no-ops. "
        "Install with: pip install redis[asyncio]"
    )


@singleton
class RedisComponent:
    """Wraps an async Redis connection pool.

    Usage via DI::

        @inject
        def __init__(self, redis: RedisComponent) -> None:
            self._redis = redis
    """

    def __init__(self, client: "aioredis.Redis | None") -> None:  # type: ignore[name-defined]
        self._client = client

    @property
    def is_enabled(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> "aioredis.Redis":  # type: ignore[name-defined]
        if self._client is None:
            raise RuntimeError(
                "Redis is not configured. Add a 'redis' section to settings.yaml."
            )
        return self._client

    # ------------------------------------------------------------------ helpers

    async def get(self, key: str) -> Optional[str]:
        if not self.is_enabled:
            return None
        try:
            value = await self._client.get(key)  # type: ignore[union-attr]
            return value.decode() if isinstance(value, bytes) else value
        except Exception as exc:
            logger.warning("Redis GET failed for key=%s: %s", key, exc)
            return None

    async def set(
        self,
        key: str,
        value: str,
        ex: Optional[int] = None,
    ) -> bool:
        if not self.is_enabled:
            return False
        try:
            await self._client.set(key, value, ex=ex)  # type: ignore[union-attr]
            return True
        except Exception as exc:
            logger.warning("Redis SET failed for key=%s: %s", key, exc)
            return False

    async def delete(self, *keys: str) -> int:
        if not self.is_enabled or not keys:
            return 0
        try:
            return await self._client.delete(*keys)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Redis DELETE failed: %s", exc)
            return 0

    async def get_json(self, key: str) -> Optional[Any]:
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def set_json(
        self, key: str, value: Any, ex: Optional[int] = None
    ) -> bool:
        try:
            serialized = json.dumps(value, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("Redis set_json serialisation failed: %s", exc)
            return False
        return await self.set(key, serialized, ex=ex)

    async def incr(self, key: str) -> int:
        if not self.is_enabled:
            return 0
        try:
            return await self._client.incr(key)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Redis INCR failed for key=%s: %s", key, exc)
            return 0

    async def expire(self, key: str, seconds: int) -> bool:
        if not self.is_enabled:
            return False
        try:
            return bool(await self._client.expire(key, seconds))  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Redis EXPIRE failed for key=%s: %s", key, exc)
            return False

    async def ttl(self, key: str) -> int:
        if not self.is_enabled:
            return -2
        try:
            return await self._client.ttl(key)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Redis TTL failed for key=%s: %s", key, exc)
            return -2

    async def exists(self, key: str) -> bool:
        if not self.is_enabled:
            return False
        try:
            return bool(await self._client.exists(key))  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Redis EXISTS failed for key=%s: %s", key, exc)
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()  # type: ignore[union-attr]
            except Exception:
                pass


def create_redis_component(settings: Settings) -> RedisComponent:
    """Factory — called from the DI container."""
    redis_cfg = getattr(settings, "redis", None)
    if redis_cfg is None or not _REDIS_AVAILABLE:
        if redis_cfg is not None:
            logger.warning("Redis configured but redis[asyncio] not installed — disabling.")
        return RedisComponent(client=None)

    try:
        client = aioredis.from_url(  # type: ignore[attr-defined]
            f"redis://{redis_cfg.host}:{redis_cfg.port}/{redis_cfg.db}",
            password=redis_cfg.password or None,
            encoding="utf-8",
            decode_responses=True,
            max_connections=redis_cfg.max_connections,
        )
        logger.info(
            "Redis connection pool initialised at %s:%s db=%s",
            redis_cfg.host,
            redis_cfg.port,
            redis_cfg.db,
        )
        return RedisComponent(client=client)
    except Exception as exc:
        logger.error("Failed to initialise Redis: %s — running without cache.", exc)
        return RedisComponent(client=None)
