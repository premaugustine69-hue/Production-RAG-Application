import logging
from typing import List, Optional
import os

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    BM25Okapi = None

from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.retrievers import BaseRetriever
from llama_index.core import VectorStoreIndex

logger = logging.getLogger(__name__)

class BM25Retriever(BaseRetriever):
    """Custom BM25 Retriever using rank-bm25."""

    def __init__(self, index: VectorStoreIndex, similarity_top_k: int = 5):
        super().__init__()
        self.index = index
        self.similarity_top_k = similarity_top_k
        self.bm25 = None
        self.nodes = []
        self._initialize_bm25()

    def _initialize_bm25(self):
        """Pre-fetch all nodes and initialize BM25Okapi."""
        if BM25Okapi is None:
            logger.error("rank-bm25 not installed. Install with `pip install rank-bm25`.")
            return

        # Fetch all documents/nodes from the index docstore
        try:
            nodes = list(self.index.docstore.docs.values())
            if not nodes:
                logger.warning("No documents found in docstore for BM25 initialization.")
                return

            self.nodes = nodes
            
            # Tokenize documents for BM25
            tokenized_corpus = [node.get_content().lower().split() for node in nodes]
            self.bm25 = BM25Okapi(tokenized_corpus)
            logger.info(f"Initialized BM25Retriever with {len(nodes)} document chunks.")
        except Exception as e:
            logger.error(f"Failed to initialize BM25: {e}")

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        """Retrieve nodes using BM25 score."""
        if not self.bm25:
            logger.error("BM25 not initialized.")
            return []

        query_tokens = query_bundle.query_str.lower().split()
        scores = self.bm25.get_scores(query_tokens)
        
        # Get top-k indices based on BM25 scores
        # We use a simple argsort for scores
        import numpy as np
        top_indices = np.argsort(scores)[-self.similarity_top_k:][::-1]
        
        results = []
        for i in top_indices:
            if scores[i] > 0:
                results.append(NodeWithScore(node=self.nodes[i], score=float(scores[i])))
                
        return results

