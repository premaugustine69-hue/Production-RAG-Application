"""High-level document storage service wrapping S3.

Uploads documents, generates pre-signed URLs, and syncs metadata to PostgreSQL.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, BinaryIO, Optional

from injector import inject, singleton

from production_rag.components.database.database_component import DatabaseComponent
from production_rag.components.database.models import DocumentMetadata
from production_rag.components.database.repository import DocumentMetadataRepository
from production_rag.components.storage.s3_component import S3Component

logger = logging.getLogger(__name__)


@singleton
class DocumentStorageService:
    """Manages raw document blobs in S3 and metadata in Postgres."""

    @inject
    def __init__(self, s3: S3Component, db: DatabaseComponent) -> None:
        self._s3 = s3
        self._db = db

    async def store_document(
        self,
        rag_doc_id: str,
        file_name: str,
        file_data: BinaryIO | bytes,
        content_type: str = "application/octet-stream",
        organization_id: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> DocumentMetadata:
        """Upload raw file to S3 and record it in Postgres."""
        if not self._s3.is_enabled or not self._db.is_enabled:
            logger.warning(
                "S3 or Postgres disabled — skipping document permanent storage for %s",
                file_name,
            )
            # Create a mock metadata object without saving it
            return DocumentMetadata(rag_doc_id=rag_doc_id, file_name=file_name)

        s3_key = f"{organization_id or 'global'}/{uuid.uuid4()}_{file_name}"
        data = file_data.read() if hasattr(file_data, "read") else file_data
        size_bytes = len(data)

        # Upload to S3
        async with self._s3.get_client() as s3_client:
            await s3_client.put_object(
                Bucket=self._s3.bucket_name,
                Key=s3_key,
                Body=data,
                ContentType=content_type,
            )
            logger.info("Uploaded %s to s3://%s/%s", file_name, self._s3.bucket_name, s3_key)

        # Store metadata in DB
        async with self._db.get_session() as session:
            repo = DocumentMetadataRepository(session)
            doc_meta = DocumentMetadata(
                organization_id=uuid.UUID(organization_id) if organization_id else None,
                rag_doc_id=rag_doc_id,
                file_name=file_name,
                file_size_bytes=size_bytes,
                content_type=content_type,
                s3_bucket=self._s3.bucket_name,
                s3_key=s3_key,
                extra_metadata=extra_metadata,
            )
            return await repo.add(doc_meta)

    async def get_download_url(self, rag_doc_id: str, expires_in: int = 3600) -> Optional[str]:
        """Generate a pre-signed URL to download the original document."""
        if not self._s3.is_enabled or not self._db.is_enabled:
            return None

        async with self._db.get_session() as session:
            repo = DocumentMetadataRepository(session)
            doc = await repo.get_by_rag_doc_id(rag_doc_id)
            if not doc or not doc.s3_bucket or not doc.s3_key:
                return None

            async with self._s3.get_client() as s3_client:
                url = await s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": doc.s3_bucket, "Key": doc.s3_key},
                    ExpiresIn=expires_in,
                )
                return str(url)

    async def delete_document(self, rag_doc_id: str) -> None:
        """Delete from S3 and remove metadata from Postgres."""
        if not self._s3.is_enabled or not self._db.is_enabled:
            return

        async with self._db.get_session() as session:
            repo = DocumentMetadataRepository(session)
            doc = await repo.get_by_rag_doc_id(rag_doc_id)
            if not doc:
                return

            # Delete from S3
            if doc.s3_bucket and doc.s3_key:
                try:
                    async with self._s3.get_client() as s3_client:
                        await s3_client.delete_object(Bucket=doc.s3_bucket, Key=doc.s3_key)
                except Exception as exc:
                    logger.warning("Failed to delete S3 object %s: %s", doc.s3_key, exc)

            # Delete from DB
            await repo.delete(doc)
