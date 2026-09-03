# Production Grade RAG Application

A production-ready AI application designed for high-accuracy document retrieval and generation (RAG). This application is built with hybrid search, re-ranking, and strict citation enforcement to ensure enterprise-level reliability.

## 🚀 Key Features

*   **Hybrid & Parent-Child Retrieval**: Combines BM25 keyword search with Vector semantic search, plus dynamic chunking via HierarchicalNodeParser for Auto-Merging Context.
*   **Context Compression & Reranking**: Uses Cross-Encoders and LongContextReorder to prioritize and compress relevant context.
*   **Citation Enforcement & Hallucination Detection**: Every response is grounded with traceability, backed by post-generation faithfulness verification.
*   **Enterprise Architecture**: Polyglot architecture featuring a Python FastAPI AI engine and a Spring Boot 3 / Java 17 enterprise services gateway.
*   **Resilience & Scale**: Asynchronous ingestion via AWS SQS background workers, S3 document storage, `pybreaker` circuit breakers, and `tenacity` retries.
*   **Security & Multi-Tenancy**: AWS Secrets Manager integration, JWT Auth, RBAC, and Postgres-backed tenant isolation.
*   **Comprehensive Observability**: OpenTelemetry tracing, Prometheus metrics, Grafana dashboards, and structured JSON CloudWatch logs.

## 🛠️ Tech Stack

*   **AI Engine**: FastAPI, LlamaIndex, Python 3.11
*   **Enterprise Services**: Spring Boot 3, Spring Security, Java 17, Maven
*   **Databases**: PostgreSQL (Alembic/JPA), Redis, Qdrant/Chroma
*   **Cloud Infrastructure**: AWS (S3, SQS, Secrets Manager, EKS, RDS, ElastiCache), Terraform
*   **Observability**: OpenTelemetry, Prometheus, Grafana, CloudWatch
*   **Testing**: Pytest, Testcontainers, k6 Load Testing

## 🚦 Getting Started

### Installation

```bash
# Install dependencies with production extras
poetry install --extras "ui llms-ollama embeddings-ollama vector-stores-qdrant"
```

### Running the Application

To run the application with the default production settings:

```bash
# Set profiles and start
$env:PGPT_PROFILES="ollama"
poetry run python -m production_rag
```

## ⚙️ Configuration

Configuration is managed via YAML profiles in the root directory:
- `settings.yaml`: Global defaults.
- `settings-ollama.yaml`: Configuration for Ollama local execution.
- `settings-local.yaml`: Configuration for local LlamaCPP/HuggingFace execution.

## 🧪 Evaluation

Run the evaluation pipeline to verify retrieval quality:

```bash
python production_rag/rag/evaluation/evaluator.py --threshold 0.7
```

## 🛡️ License

This project is licensed under the Apache-2.0 License.

