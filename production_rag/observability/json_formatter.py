"""Structured JSON logging with OpenTelemetry trace context injection.

This module configures a JSON log formatter that embeds trace/span IDs
and the correlation_id (from request state) into every log record.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON for CloudWatch/ELK ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self._format_time(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Inject OpenTelemetry trace context if available
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            ctx = span.get_span_context()
            if ctx.is_valid:
                log_entry["trace_id"] = format(ctx.trace_id, "032x")
                log_entry["span_id"] = format(ctx.span_id, "016x")
        except ImportError:
            pass

        # Inject correlation_id from thread-local (set by middleware)
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        # Exception info
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Any extra fields added by logger.info("msg", extra={"foo": "bar"})
        for key, value in record.__dict__.items():
            if key not in {
                "message", "msg", "args", "exc_info", "exc_text", "stack_info",
                "levelname", "levelno", "pathname", "filename", "module",
                "funcName", "created", "msecs", "relativeCreated", "thread",
                "threadName", "processName", "process", "name", "lineno",
                "taskName",
            } and not key.startswith("_"):
                log_entry.setdefault(key, value)

        return json.dumps(log_entry, default=str)

    @staticmethod
    def _format_time(record: logging.LogRecord) -> str:
        return time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
        ) + f".{int(record.msecs):03d}Z"
