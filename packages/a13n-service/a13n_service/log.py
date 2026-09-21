"""a13n Service process logging assembled from shared primitives."""

from __future__ import annotations

import logging
import logging.config
from typing import Any

from a13n_logging import build_logging_config, exception_details
from opentelemetry import trace

from a13n_service.settings import Settings

_LOGGER_NAMES = (
    "alembic",
    "a13n_service",
    "a13n_harness",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
)


def _context(settings: Settings) -> dict[str, str]:
    return {
        "service": settings.service.name,
        "role": settings.service.role.value,
        "build_version": settings.service.build_version,
    }


class RequestTargetFilter(logging.Filter):
    """Uvicorn access records must not retain protocol secrets in query strings."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, target, version, status = record.args
            if isinstance(target, str):
                record.args = (address, method, target.split("?", 1)[0], version, status)
        return True


class TraceContextFilter(logging.Filter):
    """Attach active OTel identifiers without coupling the logging package to OTel."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            if not hasattr(record, "trace_id"):
                record.trace_id = trace.format_trace_id(context.trace_id)
            if not hasattr(record, "span_id"):
                record.span_id = trace.format_span_id(context.span_id)
        return True


class SafeExceptionFilter(logging.Filter):
    """Project server-boundary exceptions without provider messages or locals."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info is not None and record.exc_info[1] is not None:
            record.exception_chain = exception_details(record.exc_info[1])
            record.exc_info = None
            record.exc_text = None
        return True


def build_log_config(settings: Settings) -> dict[str, Any]:
    """Build one shared logging configuration for the app and Uvicorn."""

    config = build_logging_config(
        level=settings.logging.level,
        log_format=settings.logging.format,
        logger_names=_LOGGER_NAMES,
        context=_context(settings),
        destination=settings.logging.destination,
        file_path=str(settings.logging.file_path) if settings.logging.file_path is not None else None,
        file_max_bytes=settings.logging.file_max_bytes,
        file_backup_count=settings.logging.file_backup_count,
    )
    config["filters"]["request_target"] = {"()": RequestTargetFilter}
    config["filters"]["trace_context"] = {"()": TraceContextFilter}
    config["filters"]["safe_exception"] = {"()": SafeExceptionFilter}
    for handler in config["handlers"].values():
        handler["filters"].extend(("trace_context", "safe_exception"))
    config["loggers"]["uvicorn.access"]["filters"] = ["request_target"]
    return config


def configure_logging(settings: Settings) -> None:
    """Configure process logging exactly once at the executable boundary."""

    logging.config.dictConfig(build_log_config(settings))
