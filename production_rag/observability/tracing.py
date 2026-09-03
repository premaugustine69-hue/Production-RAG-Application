"""OpenTelemetry tracer bootstrap.

Call `configure_tracing()` once at application startup before creating the
FastAPI app.  If the OTEL SDK is not installed the function is a no-op so the
app continues to work in local dev without the full observability stack.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def configure_tracing(service_name: str = "fastapi-ai") -> None:
    """Initialise OTLP trace exporter and instrument FastAPI / SQLAlchemy."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
    except ImportError as exc:
        logger.warning(
            "OpenTelemetry packages not installed — tracing disabled. (%s)", exc
        )
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Auto-instrument common libraries
    FastAPIInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()

    logger.info(
        "OpenTelemetry tracing configured — service=%s endpoint=%s",
        service_name, endpoint,
    )
