"""Redis sliding-window rate limiter.

Implements the fixed-window counter algorithm with a per-window TTL.
Degrades gracefully (allow all) when Redis is unavailable.

Key format: ``rl:{identifier}:{window_start_epoch}``
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from production_rag.components.cache.redis_component import RedisComponent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # Unix epoch when the window resets


class RateLimiter:
    """Fixed-window counter rate limiter backed by Redis.

    Args:
        redis:       RedisComponent instance.
        requests:    Maximum requests per window.
        window_secs: Window duration in seconds.
        key_prefix:  Namespace prefix for Redis keys.
    """

    def __init__(
        self,
        redis: RedisComponent,
        requests: int = 60,
        window_secs: int = 60,
        key_prefix: str = "rl",
    ) -> None:
        self._redis = redis
        self._requests = requests
        self._window_secs = window_secs
        self._key_prefix = key_prefix

    def _window_key(self, identifier: str) -> tuple[str, int]:
        """Return (redis_key, window_reset_epoch)."""
        now = int(time.time())
        window_start = now - (now % self._window_secs)
        window_reset = window_start + self._window_secs
        key = f"{self._key_prefix}:{identifier}:{window_start}"
        return key, window_reset

    async def check(self, identifier: str) -> RateLimitResult:
        """Check whether ``identifier`` is within rate limits.

        If Redis is unavailable, always returns *allowed=True* to avoid
        blocking legitimate traffic during cache outages.
        """
        if not self._redis.is_enabled:
            return RateLimitResult(
                allowed=True,
                limit=self._requests,
                remaining=self._requests,
                reset_at=int(time.time()) + self._window_secs,
            )

        key, reset_at = self._window_key(identifier)
        try:
            current = await self._redis.incr(key)
            if current == 1:
                # First request in this window — set expiry
                await self._redis.expire(key, self._window_secs)
            remaining = max(0, self._requests - current)
            allowed = current <= self._requests
            if not allowed:
                logger.warning(
                    "Rate limit exceeded for identifier=%s (count=%s, limit=%s)",
                    identifier,
                    current,
                    self._requests,
                )
            return RateLimitResult(
                allowed=allowed,
                limit=self._requests,
                remaining=remaining,
                reset_at=reset_at,
            )
        except Exception as exc:
            logger.warning(
                "Rate limiter Redis error for identifier=%s: %s — allowing request.",
                identifier,
                exc,
            )
            return RateLimitResult(
                allowed=True,
                limit=self._requests,
                remaining=self._requests,
                reset_at=reset_at,
            )
