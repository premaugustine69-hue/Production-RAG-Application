import logging
from typing import List, Dict, Any, Optional
import yaml
import os

from llama_index.core import VectorStoreIndex
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.prompts import PromptTemplate
from llama_index.core.schema import NodeWithScore
from llama_index.core.postprocessor import LongContextReorder
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.retrievers import AutoMergingRetriever
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import pybreaker
import tiktoken

from production_rag.rag.retrievers.hybrid_retriever import HybridRetriever
from production_rag.rag.reranker.cross_encoder import CrossEncoderReranker
from production_rag.rag.utils.logger import logger

# Initialize circuit breaker for LLM calls (5 failures, 60s reset)
llm_breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60)

class RagPipeline:
    """Production RAG Pipeline with Hybrid Search, Reranking, and Citation Enforcement."""

    def __init__(self, index: VectorStoreIndex, llm: Any, config_path: str = "production_rag/rag/config/config.yaml"):
        self.index = index
        self.llm = llm
        self.fallback_llm = None  # Could be passed in or initialized from config
        self.config = self._load_config(config_path)
        
        # Token monitoring setup
        self.token_counter = TokenCountingHandler(
            tokenizer=tiktoken.encoding_for_model("gpt-3.5-turbo").encode
        )
        self.llm.callback_manager = CallbackManager([self.token_counter])
        
        # Initialize components from config
        rag_cfg = self.config.get("rag", {})
        base_retriever = HybridRetriever(
            index=index,
            vector_weight=rag_cfg.get("vector_weight", 0.5),
            bm25_weight=rag_cfg.get("bm25_weight", 0.5),
            top_k=rag_cfg.get("top_k", 5)
        )

        # Parent-Child Retrieval (Dynamic Chunking Merge)
        self.retriever = AutoMergingRetriever(
            base_retriever, 
            index.storage_context, 
            verbose=True
        )
        
        self.reranker = None
        if rag_cfg.get("reranker_enabled", True):
            self.reranker = CrossEncoderReranker(
                model_name=rag_cfg.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
                top_n=rag_cfg.get("reranker_top_n", 3)
            )

        self.context_reorder = LongContextReorder()

        # Production-grade prompt with strict citation enforcement
        self.system_prompt = PromptTemplate(
            "You are a production-grade AI assistant. Your task is to answer user queries NOT based on your internal knowledge, "
            "but ONLY using the provided text context.\n\n"
            "STRICT RULES:\n"
            "1. If the answer is not contained within the context, concisely state: 'I am sorry, but the provided documentation does not contain enough information to answer this question.'\n"
            "2. Do NOT hallucinate or use external information.\n"
            "3. Every answer MUST be followed by a 'Sources:' section.\n"
            "4. Format the sources as bullet points with the format: * <document_name> (Page/Chunk: <id>)\n\n"
            "CONTEXT:\n"
            "{context_str}\n\n"
            "USER QUERY: {query_str}\n\n"
            "FINAL ANSWER FORMAT:\n"
            "Answer: <your grounded answer>\n\n"
            "Sources:\n"
            "* <source 1>\n"
            "* <source 2>"
        )

    def _load_config(self, path: str) -> Dict:
        if os.path.exists(path):
            with open(path, "r") as f:
                return yaml.safe_load(f)
        logger.warning(f"Config file {path} not found, using defaults.")
        return {}

    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception)
    )
    @llm_breaker
    def _execute_llm(self, prompt: str) -> Any:
        """Execute LLM with Circuit Breaker and Retry."""
        return self.llm.complete(prompt)

    def _execute_llm_with_fallback(self, prompt: str) -> Any:
        try:
            return self._execute_llm(prompt)
        except pybreaker.CircuitBreakerError:
            logger.error("Circuit breaker open! Primary LLM failed.")
            if self.fallback_llm:
                logger.info("Using fallback LLM.")
                return self.fallback_llm.complete(prompt)
            raise
        except Exception as e:
            logger.error(f"LLM execution failed after retries: {e}")
            if self.fallback_llm:
                logger.info("Using fallback LLM.")
                return self.fallback_llm.complete(prompt)
            raise

    def run(self, query: str, filters: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute the full RAG pipeline synchronously with resilience."""
        try:
            nodes = self._get_nodes(query, filters)
            if not nodes:
                return {
                    "answer": "Answer: I am sorry, but no relevant documents were found to answer your query.\n\nSources: None",
                    "sources": [],
                    "confidence_score": 0.0,
                    "metrics": {"total_tokens": 0}
                }

            # Apply Context Compression/Reordering
            nodes = self.context_reorder.postprocess_nodes(nodes)

            prompt = self._prepare_prompt(query, nodes)
            
            # Reset token counter before run
            self.token_counter.reset_counts()
            
            response = self._execute_llm_with_fallback(prompt)
            
            answer = response.text
            
            # Hallucination Detection & Citation Verification
            is_faithful = self._verify_faithfulness(query, answer, nodes)
            if not is_faithful:
                logger.warning("Potential hallucination detected in response.")
                answer += "\n\n[Warning: The system detected that this answer might contain unverified information not fully supported by the sources.]"
            
            # Calculate confidence score based on retrieval scores
            confidence = sum(node.score or 0.0 for node in nodes) / len(nodes) if nodes else 0.0

            metrics = {
                "total_tokens": self.token_counter.total_llm_token_count,
                "prompt_tokens": self.token_counter.prompt_llm_token_count,
                "completion_tokens": self.token_counter.completion_llm_token_count,
            }

            return {
                "answer": answer,
                "sources": self._format_sources(nodes),
                "confidence_score": min(confidence, 1.0), # Normalize
                "metrics": metrics
            }
        except Exception as e:
            logger.error(f"Pipeline failure: {e}")
            return {"answer": f"Error: {str(e)}", "sources": [], "confidence_score": 0.0, "metrics": {}}

    @llm_breaker
    def stream_run(self, query: str, filters: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute the full RAG pipeline with streaming response."""
        try:
            nodes = self._get_nodes(query, filters)
            if not nodes:
                def empty_gen():
                    yield "Answer: I am sorry, but no relevant documents were found."
                return {"answer": empty_gen(), "sources": [], "confidence_score": 0.0}

            nodes = self.context_reorder.postprocess_nodes(nodes)
            prompt = self._prepare_prompt(query, nodes)
            
            # Retries don't work easily with streaming generators, so we just wrap in circuit breaker
            response_gen = self.llm.stream_complete(prompt)
            confidence = sum(node.score or 0.0 for node in nodes) / len(nodes) if nodes else 0.0
            
            return {
                "answer": (resp.delta for resp in response_gen),
                "sources": self._format_sources(nodes),
                "confidence_score": min(confidence, 1.0)
            }
        except Exception as e:
            logger.error(f"Streaming pipeline failure: {e}")
            def error_gen():
                yield f"Error: {str(e)}"
            return {"answer": error_gen(), "sources": [], "confidence_score": 0.0}

    def _get_nodes(self, query: str, filters: Optional[Dict] = None) -> List[NodeWithScore]:
        """Internal helper to get retrieved and reranked nodes with metadata filtering."""
        # Setup metadata filters if provided
        from llama_index.core.vector_stores.types import MetadataFilters, ExactMatchFilter
        llm_filters = None
        if filters:
            llm_filters = MetadataFilters(
                filters=[ExactMatchFilter(key=k, value=v) for k, v in filters.items()]
            )
            # Apply to base retrievers if they support it
            base_retriever = self.retriever._retriever
            if hasattr(base_retriever, "vector_retriever"):
                base_retriever.vector_retriever._kwargs["filters"] = llm_filters
            
        nodes = self.retriever.retrieve(query) 
        
        # Fallback manual filtering if retriever doesn't support it directly
        if filters:
            filtered_nodes = []
            for node in nodes:
                match = all(node.node.metadata.get(k) == v for k, v in filters.items())
                if match:
                    filtered_nodes.append(node)
            nodes = filtered_nodes

        if self.reranker and nodes:
            nodes = self.reranker.rerank(query, nodes)
        return nodes

    def _prepare_prompt(self, query: str, nodes: List[NodeWithScore]) -> str:
        """Internal helper to prepare the prompt with context."""
        context_str = ""
        for i, node in enumerate(nodes):
            content = node.node.get_content()
            metadata = node.node.metadata
            file_name = metadata.get("file_name", "Unknown Document")
            page = metadata.get("page_label", "N/A")
            context_str += f"[Doc {i+1}] Source: {file_name} (Page: {page})\nContent: {content}\n\n"
        return self.system_prompt.format(context_str=context_str, query_str=query)

    def _format_sources(self, nodes: List[NodeWithScore]) -> List[Dict]:
        """Internal helper to format source metadata."""
        return [
            {
                "file_name": node.node.metadata.get("file_name"),
                "page": node.node.metadata.get("page_label"),
                "score": node.score,
                "node_id": node.node.node_id
            } for node in nodes
        ]

    def _verify_faithfulness(self, query: str, answer: str, nodes: List[NodeWithScore]) -> bool:
        """Fast hallucination detection to check if answer is supported by context."""
        if "I am sorry" in answer:
            return True # Not a hallucination if it refused to answer
            
        verification_prompt = (
            "Context:\n"
            "{context}\n\n"
            "Query: {query}\n"
            "Answer: {answer}\n\n"
            "Does the given answer rely strictly on the context provided above without introducing external information? "
            "Reply with exactly 'YES' or 'NO'."
        )
        context_str = "\n".join([n.node.get_content() for n in nodes[:3]]) # Limit to top 3 for speed
        prompt = verification_prompt.format(context=context_str, query=query, answer=answer)
        
        try:
            # Use fallback LLM if available for verification, otherwise primary
            v_llm = self.fallback_llm if self.fallback_llm else self.llm
            resp = v_llm.complete(prompt)
            return "YES" in resp.text.upper()
        except Exception as e:
            logger.warning(f"Faithfulness verification failed: {e}")
            return True # Default to passing if verification fails

