"""Async S3 client component.

Provides a unified connection to AWS S3 or compatible APIs (Localstack, MinIO).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from injector import inject, singleton

from production_rag.settings.settings import Settings

logger = logging.getLogger(__name__)

try:
    import aioboto3  # type: ignore[import]
    _AIOBOTO3_AVAILABLE = True
except ImportError:
    _AIOBOTO3_AVAILABLE = False
    logger.warning("aioboto3 not installed. S3 features will be unavailable.")


@singleton
class S3Component:
    """Provides async S3 clients via aioboto3 Session."""

    @inject
    def __init__(self, settings: Settings) -> None:
        self._enabled = False
        self._session = None
        self._bucket_name = None
        self._endpoint_url = None
        self._region = None

        if not _AIOBOTO3_AVAILABLE:
            return

        aws = getattr(settings, "aws", None)
        if aws and aws.s3:
            self._enabled = True
            self._session = aioboto3.Session()
            self._bucket_name = aws.s3.bucket_name
            self._endpoint_url = aws.s3.endpoint_url
            self._region = aws.s3.region
            logger.info(
                "Initialised S3Component for bucket=%s region=%s",
                self._bucket_name,
                self._region,
            )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def bucket_name(self) -> str:
        if not self._enabled:
            raise RuntimeError("S3 is not configured.")
        return self._bucket_name  # type: ignore[return-value]

    @asynccontextmanager
    async def get_client(self) -> AsyncGenerator[Any, None]:
        """Context manager yielding an async S3 client."""
        if not self._enabled or self._session is None:
            raise RuntimeError("S3 is not configured in settings.yaml (aws.s3).")
        
        async with self._session.client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
        ) as client:
            yield client
