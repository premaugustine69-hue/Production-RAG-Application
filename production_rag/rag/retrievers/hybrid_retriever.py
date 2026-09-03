import logging
from typing import List, Dict, Optional
from llama_index.core.schema import NodeWithScore, QueryBundle
from .vector_retriever import VectorRetriever
from .bm25_retriever import BM25Retriever
from llama_index.core import VectorStoreIndex

from llama_index.core.retrievers import BaseRetriever

logger = logging.getLogger(__name__)

class HybridRetriever(BaseRetriever):
    """Combines BM25 and Vector retrieval scores."""

    def __init__(
        self, 
        index: VectorStoreIndex, 
        vector_weight: float = 0.5, 
        bm25_weight: float = 0.5, 
        top_k: int = 5
    ):
        super().__init__()
        self.vector_retriever = VectorRetriever(index, similarity_top_k=top_k * 2)
        self.bm25_retriever = BM25Retriever(index, similarity_top_k=top_k * 2)
        self.vector_weight = vector_weight
        self.bm25_weight = bm25_weight
        self.top_k = top_k

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        """Hybrid search combining vector and BM25 search."""
        query = query_bundle.query_str
        logger.info(f"Performing hybrid retrieval for query: {query}")
        
        # Get results from both retrievers
        vector_nodes = self.vector_retriever.retrieve(query)
        bm25_nodes = self.bm25_retriever.retrieve(QueryBundle(query_str=query))
        
        # Normalize scores to [0, 1] range for both retrievers before combining
        def normalize_nodes(nodes):
            if not nodes:
                return {}
            max_score = max(node.score for node in nodes) if nodes else 1.0
            min_score = min(node.score for node in nodes) if nodes else 0.0
            score_range = max_score - min_score if max_score > min_score else 1.0
            
            # Map node ID to normalized score
            results = {}
            for node in nodes:
                norm_score = (node.score - min_score) / score_range
                results[node.node.node_id] = (node.node, norm_score)
            return results

        norm_vector = normalize_nodes(vector_nodes)
        norm_bm25 = normalize_nodes(bm25_nodes)
        
        # Merge results using weights
        combined_scores = {}
        all_node_ids = set(norm_vector.keys()) | set(norm_bm25.keys())
        
        for node_id in all_node_ids:
            node_obj = None
            v_score = 0.0
            b_score = 0.0
            
            if node_id in norm_vector:
                node_obj, v_score = norm_vector[node_id]
            if node_id in norm_bm25:
                # Always prioritize the object if it wasn't set yet
                node_obj = norm_bm25[node_id][0] if not node_obj else node_obj
                b_score = norm_bm25[node_id][1]
            
            # Final score based on weights
            combined_scores[node_id] = (node_obj, (v_score * self.vector_weight) + (b_score * self.bm25_weight))
            
        # Re-rank based on combined score
        sorted_results = sorted(
            combined_scores.items(), key=lambda x: x[1][1], reverse=True
        )[:self.top_k]
        
        # Convert back to NodeWithScore
        hybrid_nodes = [
            NodeWithScore(node=node_obj, score=score) 
            for node_id, (node_obj, score) in sorted_results
        ]
        
        logger.info(f"Retrieved {len(hybrid_nodes)} nodes using hybrid search.")
        return hybrid_nodes

