"""Async SQS client component.

Provides an aioboto3-based interface to AWS SQS for decoupling heavy
background jobs (ingestion, embeddings, evaluation) from the HTTP layer.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from injector import inject, singleton

from production_rag.settings.settings import Settings

logger = logging.getLogger(__name__)

try:
    import aioboto3  # type: ignore[import]
    _AIOBOTO3_AVAILABLE = True
except ImportError:
    _AIOBOTO3_AVAILABLE = False
    logger.warning("aioboto3 not installed. SQS features will be unavailable.")


@singleton
class SQSComponent:
    """Provides async SQS clients via aioboto3 Session."""

    @inject
    def __init__(self, settings: Settings) -> None:
        self._enabled = False
        self._session = None
        self._ingestion_queue_url = None
        self._endpoint_url = None
        self._region = None

        if not _AIOBOTO3_AVAILABLE:
            return

        aws = getattr(settings, "aws", None)
        if aws and aws.sqs:
            self._enabled = True
            self._session = aioboto3.Session()
            self._ingestion_queue_url = aws.sqs.ingestion_queue_url
            self._endpoint_url = aws.sqs.endpoint_url
            self._region = aws.sqs.region
            logger.info(
                "Initialised SQSComponent for region=%s",
                self._region,
            )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def ingestion_queue_url(self) -> str:
        if not self._enabled:
            raise RuntimeError("SQS is not configured.")
        return self._ingestion_queue_url  # type: ignore[return-value]

    @asynccontextmanager
    async def get_client(self) -> AsyncGenerator[Any, None]:
        """Context manager yielding an async SQS client."""
        if not self._enabled or self._session is None:
            raise RuntimeError("SQS is not configured in settings.yaml (aws.sqs).")
        
        async with self._session.client(
            "sqs",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
        ) as client:
            yield client

    async def send_message(self, queue_url: str, message_body: dict[str, Any], delay_seconds: int = 0) -> str | None:
        """Helper to send a JSON payload to SQS."""
        if not self._enabled:
            logger.warning("SQS disabled. Skipping message to %s", queue_url)
            return None
        
        try:
            async with self.get_client() as sqs:
                response = await sqs.send_message(
                    QueueUrl=queue_url,
                    MessageBody=json.dumps(message_body, default=str),
                    DelaySeconds=delay_seconds,
                )
                return str(response.get("MessageId"))
        except Exception as exc:
            logger.error("Failed to send SQS message: %s", exc)
            return None
