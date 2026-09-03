import logging
from typing import List, Optional
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.postprocessor import SentenceTransformerRerank

logger = logging.getLogger(__name__)

class CrossEncoderReranker:
    """Performs reranking on top-k retrieved nodes using cross-encoders."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", top_n: int = 3):
        self._model_name = model_name
        self._top_n = top_n
        self._reranker = SentenceTransformerRerank(
            model=model_name,
            top_n=top_n
        )

    def rerank(self, query: str, nodes: List[NodeWithScore]) -> List[NodeWithScore]:
        """Reranks the retrieved nodes based on cross-encoder similarity with query."""
        if not nodes:
            logger.warning("No nodes provided for reranking.")
            return []
            
        logger.info(f"Reranking {len(nodes)} nodes using {self._model_name}...")
        query_bundle = QueryBundle(query_str=query)
        
        # SentenceTransformerRerank from llama-index handles batch processing
        reranked_nodes = self._reranker.postprocess_nodes(nodes, query_bundle)
        
        logger.info(f"Reranked results reduced to top {len(reranked_nodes)} nodes.")
        return reranked_nodes

