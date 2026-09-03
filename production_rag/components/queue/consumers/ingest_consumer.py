"""SQS consumer / worker process for async document ingestion.

Polls the ingestion queue, downloads raw documents from S3 (if applicable),
and runs the LlamaIndex ingestion pipeline. Failures are automatically
retried up to the SQS MaxReceiveCount before landing in the DLQ.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from injector import Injector

from production_rag.components.queue.sqs_component import SQSComponent
from production_rag.components.storage.s3_component import S3Component
from production_rag.server.ingest.ingest_service import IngestService

logger = logging.getLogger(__name__)


class IngestWorker:
    """Background worker that consumes SQS ingestion jobs."""

    def __init__(self, injector: Injector) -> None:
        self._injector = injector
        self._sqs = injector.get(SQSComponent)
        self._s3 = injector.get(S3Component)
        self._ingest_service = injector.get(IngestService)
        self._running = False

    async def start(self) -> None:
        """Start the long-polling loop."""
        if not self._sqs.is_enabled:
            logger.warning("SQS is disabled — IngestWorker will not run.")
            return

        self._running = True
        queue_url = self._sqs.ingestion_queue_url
        logger.info("Starting IngestWorker on queue: %s", queue_url)

        async with self._sqs.get_client() as client:
            while self._running:
                try:
                    response = await client.receive_message(
                        QueueUrl=queue_url,
                        MaxNumberOfMessages=1,
                        WaitTimeSeconds=20,
                        MessageAttributeNames=["All"],
                    )
                    messages = response.get("Messages", [])
                    for msg in messages:
                        await self._process_message(client, queue_url, msg)
                except Exception as exc:
                    logger.error("Error polling SQS: %s", exc)
                    await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False

    async def _process_message(self, client: Any, queue_url: str, msg: dict[str, Any]) -> None:
        receipt_handle = msg["ReceiptHandle"]
        body = msg.get("Body", "{}")
        try:
            payload = json.loads(body)
            action = payload.get("action")
            if action != "ingest":
                logger.warning("Unknown action %s — dropping message.", action)
                await self._delete_message(client, queue_url, receipt_handle)
                return

            await self._execute_ingestion(payload)
            # Success — remove from queue
            await self._delete_message(client, queue_url, receipt_handle)

        except Exception as exc:
            # Let it visibility-timeout and retry (DLQ handled by SQS config)
            logger.error("Failed to process ingestion job: %s", exc)

    async def _execute_ingestion(self, payload: dict[str, Any]) -> None:
        rag_doc_id = payload["rag_doc_id"]
        file_name = payload["file_name"]
        s3_bucket = payload.get("s3_bucket")
        s3_key = payload.get("s3_key")
        text_content = payload.get("text_content")

        logger.info("Worker processing ingestion for doc=%s", rag_doc_id)

        if text_content is not None:
            # Run text ingestion in threadpool to avoid blocking event loop
            await asyncio.to_thread(self._ingest_service.ingest_text, file_name, text_content)
            return

        if s3_bucket and s3_key and self._s3.is_enabled:
            # Download from S3 to a temp file, then ingest
            import tempfile
            from pathlib import Path
            
            async with self._s3.get_client() as s3_client:
                response = await s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
                data = await response["Body"].read()

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                try:
                    path_to_tmp = Path(tmp.name)
                    path_to_tmp.write_bytes(data)
                    await asyncio.to_thread(self._ingest_service.ingest_file, file_name, path_to_tmp)
                finally:
                    tmp.close()
                    path_to_tmp.unlink()
            return
            
        logger.warning("Worker received ingestion job without valid text or S3 info.")

    async def _delete_message(self, client: Any, queue_url: str, receipt_handle: str) -> None:
        await client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
