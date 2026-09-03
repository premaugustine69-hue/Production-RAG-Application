"""FastAPI app creation, logger configuration and main API routes."""

import logging
import time
import uuid

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from injector import Injector
from llama_index.core.callbacks import CallbackManager
from llama_index.core.callbacks.global_handlers import create_global_handler
from llama_index.core.settings import Settings as LlamaIndexSettings

from production_rag.server.auth.auth_router import auth_router
from production_rag.server.chat.chat_router import chat_router
from production_rag.server.chunks.chunks_router import chunks_router
from production_rag.server.completions.completions_router import completions_router
from production_rag.server.embeddings.embeddings_router import embeddings_router
from production_rag.server.health.health_router import health_router
from production_rag.server.ingest.ingest_router import ingest_router
from production_rag.server.recipes.summarize.summarize_router import summarize_router
from production_rag.settings.settings import Settings

from production_rag.observability.tracing import configure_tracing
from production_rag.observability.metrics import configure_metrics

logger = logging.getLogger(__name__)


def create_app(root_injector: Injector) -> FastAPI:
    # Bootstrap OpenTelemetry tracing before the app is assembled
    configure_tracing(service_name="fastapi-ai")


    # Start the API
    async def bind_injector_to_request(request: Request) -> None:
        request.state.injector = root_injector

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio
        from production_rag.components.queue.consumers.ingest_consumer import IngestWorker
        
        worker = IngestWorker(root_injector)
        worker_task = asyncio.create_task(worker.start())
        yield
        worker.stop()
        try:
            # Wait for worker to finish current loop gracefully (with timeout to not block shutdown)
            await asyncio.wait_for(worker_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    app = FastAPI(
        title="Enterprise RAG Knowledge Platform",
        description=(
            "Production-grade Retrieval Augmented Generation with hybrid search, "
            "reranking, citation enforcement, multi-tenant auth, and Redis caching."
        ),
        version="2.0.0",
        dependencies=[Depends(bind_injector_to_request)],
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ routers
    # Existing routers — untouched
    app.include_router(completions_router)
    app.include_router(chat_router)
    app.include_router(chunks_router)
    app.include_router(ingest_router)
    app.include_router(summarize_router)
    app.include_router(embeddings_router)
    app.include_router(health_router)

    # Phase 3: Auth router
    app.include_router(auth_router)

    # -------------------------------------------------- LlamaIndex observability
    global_handler = create_global_handler("simple")
    if global_handler:
        LlamaIndexSettings.callback_manager = CallbackManager([global_handler])

    settings = root_injector.get(Settings)

    # Phase 8: Prometheus metrics endpoint
    configure_metrics(app)

    # ------------------------------------------------------------ CORS middleware
    if settings.server.cors.enabled:
        logger.debug("Setting up CORS middleware")
        app.add_middleware(
            CORSMiddleware,
            allow_credentials=settings.server.cors.allow_credentials,
            allow_origins=settings.server.cors.allow_origins,
            allow_origin_regex=settings.server.cors.allow_origin_regex,
            allow_methods=settings.server.cors.allow_methods,
            allow_headers=settings.server.cors.allow_headers,
        )

    # ------------------------------------------------- Correlation ID middleware
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        """Attach a correlation ID to every request/response and inject into log context."""
        import logging
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        start = time.monotonic()
        # Inject into all log records for this request via a filter
        class _CorrelationFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                record.correlation_id = correlation_id  # type: ignore[attr-defined]
                return True
        _filter = _CorrelationFilter()
        root_logger = logging.getLogger()
        root_logger.addFilter(_filter)
        try:
            response: Response = await call_next(request)
        finally:
            root_logger.removeFilter(_filter)
        latency_ms = int((time.monotonic() - start) * 1000)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time-Ms"] = str(latency_ms)
        return response

    # -------------------------------------------------- Redis rate-limit middleware
    redis_cfg = settings.redis
    if redis_cfg is not None:
        from production_rag.components.cache.rate_limiter import RateLimiter
        from production_rag.components.cache.redis_component import RedisComponent

        redis_component: RedisComponent = root_injector.get(RedisComponent)
        rate_limiter = RateLimiter(
            redis=redis_component,
            requests=redis_cfg.rate_limit_requests,
            window_secs=redis_cfg.rate_limit_window_secs,
        )

        @app.middleware("http")
        async def rate_limit_middleware(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
            """Per-IP sliding-window rate limiter — bypassed when Redis is down."""
            # Skip rate limiting for health check and auth login
            skip_paths = {"/health", "/v1/auth/login", "/v1/auth/refresh"}
            if request.url.path in skip_paths:
                return await call_next(request)

            client_ip = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or (request.client.host if request.client else "unknown")
            )
            result = await rate_limiter.check(client_ip)
            if not result.allowed:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please slow down.",
                        "retry_after": result.reset_at,
                    },
                    headers={
                        "X-RateLimit-Limit": str(result.limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(result.reset_at),
                        "Retry-After": str(result.reset_at),
                    },
                )
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(result.limit)
            response.headers["X-RateLimit-Remaining"] = str(result.remaining)
            response.headers["X-RateLimit-Reset"] = str(result.reset_at)
            return response

        logger.info(
            "Redis rate limiting enabled: %d req/%ds",
            redis_cfg.rate_limit_requests,
            redis_cfg.rate_limit_window_secs,
        )

    # --------------------------------------------------------------- Gradio UI
    if settings.ui.enabled:
        logger.debug("Importing the UI module")
        try:
            from production_rag.ui.ui import ProductionGradeRAGApplicationUI
        except ImportError as e:
            raise ImportError(
                "UI dependencies not found, install with `poetry install --extras ui`"
            ) from e

        ui = root_injector.get(ProductionGradeRAGApplicationUI)
        ui.mount_in_app(app, settings.ui.path)

    return app
