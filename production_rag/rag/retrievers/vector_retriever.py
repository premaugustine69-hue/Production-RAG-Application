from typing import List
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core import VectorStoreIndex

class VectorRetriever:
    """A wrapper for vector similarity retrieval."""

    def __init__(self, index: VectorStoreIndex, similarity_top_k: int = 5):
        self._retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=similarity_top_k,
        )

    def retrieve(self, query: str) -> List[NodeWithScore]:
        """Performs vector search."""
        query_bundle = QueryBundle(query_str=query)
        return self._retriever.retrieve(query_bundle)

