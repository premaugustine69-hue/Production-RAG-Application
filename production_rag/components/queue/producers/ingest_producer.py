"""SQS producer for async document ingestion.

Pushes a message to the ingestion queue containing the document ID, S3 key,
and metadata so the worker process can pick it up.
"""

from __future__ import annotations

import logging
from typing import Optional

from injector import inject, singleton

from production_rag.components.queue.sqs_component import SQSComponent

logger = logging.getLogger(__name__)


@singleton
class IngestProducer:
    """Publishes document ingestion jobs to SQS."""

    @inject
    def __init__(self, sqs: SQSComponent) -> None:
        self._sqs = sqs

    async def queue_ingestion(
        self,
        rag_doc_id: str,
        file_name: str,
        organization_id: Optional[str] = None,
        s3_bucket: Optional[str] = None,
        s3_key: Optional[str] = None,
        text_content: Optional[str] = None,
    ) -> Optional[str]:
        """Queue a document for ingestion.

        If `text_content` is provided, it's a raw text ingestion without S3.
        Otherwise, the worker will download from `s3_bucket`/`s3_key`.
        """
        if not self._sqs.is_enabled:
            return None

        payload = {
            "action": "ingest",
            "rag_doc_id": rag_doc_id,
            "file_name": file_name,
            "organization_id": organization_id,
            "s3_bucket": s3_bucket,
            "s3_key": s3_key,
            "text_content": text_content,
        }

        logger.info("Queueing ingestion for doc=%s", rag_doc_id)
        return await self._sqs.send_message(
            self._sqs.ingestion_queue_url, payload
        )
