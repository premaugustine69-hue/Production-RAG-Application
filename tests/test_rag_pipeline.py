import pytest
from unittest.mock import Mock, MagicMock
from llama_index.core import VectorStoreIndex
from production_rag.rag.pipeline.rag_pipeline import RagPipeline
from llama_index.core.schema import NodeWithScore, Document, TextNode
import pybreaker

@pytest.fixture
def mock_index():
    index = MagicMock(spec=VectorStoreIndex)
    index.storage_context = MagicMock()
    return index

@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.complete.return_value = MagicMock(text="This is a mock answer.")
    return llm

def test_rag_pipeline_run_success(mock_index, mock_llm):
    pipeline = RagPipeline(index=mock_index, llm=mock_llm)
    
    # Mock retrieval
    node1 = NodeWithScore(node=TextNode(text="Context 1", id_="1"), score=0.9)
    pipeline.retriever.retrieve = Mock(return_value=[node1])
    
    # Run pipeline
    result = pipeline.run("What is this?")
    
    assert "answer" in result
    assert result["answer"].startswith("This is a mock answer.")
    assert "sources" in result
    assert len(result["sources"]) == 1
    assert result["confidence_score"] == 0.9
    assert "metrics" in result

def test_rag_pipeline_fallback(mock_index, mock_llm):
    # Make primary LLM fail repeatedly
    mock_llm.complete.side_effect = Exception("Primary failed")
    
    fallback_llm = MagicMock()
    fallback_llm.complete.return_value = MagicMock(text="Fallback answer.")
    
    pipeline = RagPipeline(index=mock_index, llm=mock_llm)
    pipeline.fallback_llm = fallback_llm
    
    # Mock retrieval
    node1 = NodeWithScore(node=TextNode(text="Context 1", id_="1"), score=0.9)
    pipeline.retriever.retrieve = Mock(return_value=[node1])
    
    # Run pipeline - should use fallback
    result = pipeline.run("What is this?")
    
    assert "answer" in result
    assert result["answer"].startswith("Fallback answer.")

def test_rag_pipeline_circuit_breaker(mock_index, mock_llm):
    mock_llm.complete.side_effect = Exception("Primary failed")
    pipeline = RagPipeline(index=mock_index, llm=mock_llm)
    
    pipeline.retriever.retrieve = Mock(return_value=[NodeWithScore(node=TextNode(text="C", id_="1"), score=0.9)])
    
    # Exhaust circuit breaker
    for _ in range(6):
        pipeline.run("Query")
        
    assert pipeline._execute_llm.breaker.current_state == pybreaker.STATE_OPEN

