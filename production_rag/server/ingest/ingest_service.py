import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, AnyStr, BinaryIO

from injector import inject, singleton
from llama_index.core.node_parser import SentenceWindowNodeParser
from llama_index.core.storage import StorageContext

from production_rag.components.embedding.embedding_component import EmbeddingComponent
from production_rag.components.ingest.ingest_component import get_ingestion_component
from production_rag.components.llm.llm_component import LLMComponent
from production_rag.components.node_store.node_store_component import NodeStoreComponent
from production_rag.components.vector_store.vector_store_component import (
    VectorStoreComponent,
)
from production_rag.components.storage.document_storage_service import DocumentStorageService
from production_rag.components.queue.producers.ingest_producer import IngestProducer
from production_rag.server.ingest.model import IngestedDoc
from production_rag.settings.settings import settings

if TYPE_CHECKING:
    from llama_index.core.storage.docstore.types import RefDocInfo

logger = logging.getLogger(__name__)


@singleton
class IngestService:
    @inject
    def __init__(
        self,
        llm_component: LLMComponent,
        vector_store_component: VectorStoreComponent,
        embedding_component: EmbeddingComponent,
        node_store_component: NodeStoreComponent,
        document_storage: DocumentStorageService,
        ingest_producer: IngestProducer,
    ) -> None:
        self.llm_service = llm_component
        self._document_storage = document_storage
        self._ingest_producer = ingest_producer
        self.storage_context = StorageContext.from_defaults(
            vector_store=vector_store_component.vector_store,
            docstore=node_store_component.doc_store,
            index_store=node_store_component.index_store,
        )
        from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes

        # Dynamic chunking with Parent-Child relationships
        node_parser = HierarchicalNodeParser.from_defaults(
            chunk_sizes=[2048, 512, 128] # Dynamic parent-child chunking
        )

        self.ingest_component = get_ingestion_component(
            self.storage_context,
            embed_model=embedding_component.embedding_model,
            transformations=[node_parser, embedding_component.embedding_model],
            settings=settings(),
        )

    def _ingest_data(self, file_name: str, file_data: AnyStr) -> list[IngestedDoc]:
        logger.debug("Got file data of size=%s to ingest", len(file_data))
        # llama-index mainly supports reading from files, so
        # we have to create a tmp file to read for it to work
        # delete=False to avoid a Windows 11 permission error.
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            try:
                path_to_tmp = Path(tmp.name)
                if isinstance(file_data, bytes):
                    path_to_tmp.write_bytes(file_data)
                else:
                    path_to_tmp.write_text(str(file_data))
                return self.ingest_file(file_name, path_to_tmp)
            finally:
                tmp.close()
                path_to_tmp.unlink()

    def ingest_file(self, file_name: str, file_data: Path) -> list[IngestedDoc]:
        logger.info("Ingesting file_name=%s", file_name)
        documents = self.ingest_component.ingest(file_name, file_data)
        logger.info("Finished ingestion file_name=%s", file_name)
        return [IngestedDoc.from_document(document) for document in documents]

    def ingest_text(self, file_name: str, text: str) -> list[IngestedDoc]:
        logger.debug("Ingesting text data with file_name=%s", file_name)
        return self._ingest_data(file_name, text)

    def ingest_bin_data(
        self, file_name: str, raw_file_data: BinaryIO
    ) -> list[IngestedDoc]:
        logger.debug("Ingesting binary data with file_name=%s", file_name)
        file_data = raw_file_data.read()
        return self._ingest_data(file_name, file_data)

    async def async_ingest_bin_data(
        self, file_name: str, raw_file_data: BinaryIO
    ) -> list[IngestedDoc]:
        """Async entry point. Uses S3 + SQS if configured, else fallback."""
        import uuid
        rag_doc_id = str(uuid.uuid4())
        
        # 1. Store in S3 (will mock and return if S3 is disabled)
        doc_meta = await self._document_storage.store_document(
            rag_doc_id=rag_doc_id,
            file_name=file_name,
            file_data=raw_file_data,
        )

        # 2. Queue in SQS
        msg_id = await self._ingest_producer.queue_ingestion(
            rag_doc_id=rag_doc_id,
            file_name=file_name,
            s3_bucket=doc_meta.s3_bucket,
            s3_key=doc_meta.s3_key,
        )

        if msg_id:
            # Async mode activated
            logger.info("Enqueued ingestion job %s for %s", msg_id, file_name)
            return [IngestedDoc(object="ingest.document", doc_id=rag_doc_id)]
        
        # 3. Fallback to synchronous if no SQS configured
        logger.info("SQS disabled, falling back to sync ingestion for %s", file_name)
        if hasattr(raw_file_data, "seek"):
            raw_file_data.seek(0)
        return self.ingest_bin_data(file_name, raw_file_data)

    async def async_ingest_text(
        self, file_name: str, text: str
    ) -> list[IngestedDoc]:
        """Async text entry point. Uses SQS if configured, else fallback."""
        import uuid
        rag_doc_id = str(uuid.uuid4())
        
        msg_id = await self._ingest_producer.queue_ingestion(
            rag_doc_id=rag_doc_id,
            file_name=file_name,
            text_content=text,
        )

        if msg_id:
            logger.info("Enqueued text ingestion job %s for %s", msg_id, file_name)
            return [IngestedDoc(object="ingest.document", doc_id=rag_doc_id)]
        
        return self.ingest_text(file_name, text)

    def bulk_ingest(self, files: list[tuple[str, Path]]) -> list[IngestedDoc]:
        logger.info("Ingesting file_names=%s", [f[0] for f in files])
        documents = self.ingest_component.bulk_ingest(files)
        logger.info("Finished ingestion file_name=%s", [f[0] for f in files])
        return [IngestedDoc.from_document(document) for document in documents]

    def list_ingested(self) -> list[IngestedDoc]:
        ingested_docs: list[IngestedDoc] = []
        try:
            docstore = self.storage_context.docstore
            ref_docs: dict[str, RefDocInfo] | None = docstore.get_all_ref_doc_info()

            if not ref_docs:
                return ingested_docs

            for doc_id, ref_doc_info in ref_docs.items():
                doc_metadata = None
                if ref_doc_info is not None and ref_doc_info.metadata is not None:
                    doc_metadata = IngestedDoc.curate_metadata(ref_doc_info.metadata)
                ingested_docs.append(
                    IngestedDoc(
                        object="ingest.document",
                        doc_id=doc_id,
                        doc_metadata=doc_metadata,
                    )
                )
        except ValueError:
            logger.warning("Got an exception when getting list of docs", exc_info=True)
            pass
        logger.debug("Found count=%s ingested documents", len(ingested_docs))
        return ingested_docs

    def delete(self, doc_id: str) -> None:
        """Delete an ingested document.

        :raises ValueError: if the document does not exist
        """
        logger.info(
            "Deleting the ingested document=%s in the doc and index store", doc_id
        )
        self.ingest_component.delete(doc_id)

