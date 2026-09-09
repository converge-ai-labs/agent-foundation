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
        "service": settings.service.name,
        "role": settings.service.role.value,
        "build_version": settings.service.build_version,
    }


def build_log_config(settings: Settings) -> dict[str, Any]:
    """Build one shared logging configuration for the app and Uvicorn."""

    return build_logging_config(
        level=settings.logging.level,
        log_format=settings.logging.format,
        logger_names=_LOGGER_NAMES,
        context=_context(settings),
    )


def configure_logging(settings: Settings) -> None:
    """Configure process logging exactly once at the executable boundary."""

    configure_process_logging(
        level=settings.logging.level,
        log_format=settings.logging.format,
        logger_names=_LOGGER_NAMES,
        context=_context(settings),
    )
