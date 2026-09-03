"""Prometheus metrics registry for FastAPI.

Import and call `configure_metrics(app)` in launcher.py to enable the
/metrics endpoint.  Silently no-ops when prometheus-client is not installed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def configure_metrics(app) -> None:  # type: ignore[no-untyped-def]
    """Mount Prometheus /metrics endpoint onto the FastAPI app."""
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:
        logger.warning(
            "prometheus-fastapi-instrumentator not installed — /metrics endpoint disabled."
        )
        return

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", tags=["Observability"])
    logger.info("Prometheus metrics enabled at /metrics")
