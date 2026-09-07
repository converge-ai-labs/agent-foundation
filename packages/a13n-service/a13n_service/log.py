"""a13n Service process logging assembled from shared primitives."""

from __future__ import annotations

from typing import Any

from a13n_logging import build_logging_config
from a13n_logging import configure_logging as configure_process_logging

from a13n_service.settings import Settings

_LOGGER_NAMES = (
    "alembic",
    "a13n_service",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
)


def _context(settings: Settings) -> dict[str, str]:
    return {
        "service": settings.service_name,
        "role": settings.role.value,
        "build_version": settings.build_version,
    }


def build_log_config(settings: Settings) -> dict[str, Any]:
    """Build one shared logging configuration for the app and Uvicorn."""

    return build_logging_config(
        level=settings.log_level,
        log_format=settings.log_format,
        logger_names=_LOGGER_NAMES,
        context=_context(settings),
    )


def configure_logging(settings: Settings) -> None:
    """Configure process logging exactly once at the executable boundary."""

    configure_process_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        logger_names=_LOGGER_NAMES,
        context=_context(settings),
    )
