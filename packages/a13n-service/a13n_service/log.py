"""a13n Service process logging assembled from shared primitives."""

from __future__ import annotations

import logging
import logging.config
from typing import Any

from a13n_logging import build_logging_config

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


def build_log_config(settings: Settings) -> dict[str, Any]:
    """Build one shared logging configuration for the app and Uvicorn."""

    config = build_logging_config(
        level=settings.logging.level,
        log_format=settings.logging.format,
        logger_names=_LOGGER_NAMES,
        context=_context(settings),
    )
    config["filters"]["request_target"] = {"()": RequestTargetFilter}
    config["loggers"]["uvicorn.access"]["filters"] = ["request_target"]
    return config


def configure_logging(settings: Settings) -> None:
    """Configure process logging exactly once at the executable boundary."""

    logging.config.dictConfig(build_log_config(settings))
