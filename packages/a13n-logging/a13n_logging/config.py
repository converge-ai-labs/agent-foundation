"""Process-boundary logging configuration with pretty and JSON output."""

from __future__ import annotations

import json
import logging
import logging.config
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from rich.logging import RichHandler

_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "highlighter",
    "level",
    "logger",
    "markup",
    "message",
    "timestamp",
}


class LogFormat(StrEnum):
    """Supported process log formats."""

    pretty = "pretty"
    json = "json"


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """Render one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))


class PrettyFormatter(logging.Formatter):
    """Render readable event-style text for RichHandler."""

    def format(self, record: logging.LogRecord) -> str:
        fields = " ".join(
            f"{key}={json.dumps(value, default=str, ensure_ascii=False, separators=(',', ':'))}"
            for key, value in sorted(_extra_fields(record).items())
        )
        message = f"{record.name} {record.getMessage()}"
        return f"{message} {fields}" if fields else message


def _logging_config(*, level: str, log_format: LogFormat, logger_names: Sequence[str]) -> dict[str, Any]:
    normalized_level = level.upper()
    handler: dict[str, Any] = (
        {"()": RichHandler, "markup": False, "rich_tracebacks": True, "show_path": False}
        if log_format is LogFormat.pretty
        else {"class": "logging.StreamHandler", "stream": "ext://sys.stdout"}
    )
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "a13n_logging.JsonFormatter"},
            "pretty": {"()": "a13n_logging.PrettyFormatter"},
        },
        "handlers": {"default": {**handler, "formatter": log_format.value, "level": normalized_level}},
        "loggers": {
            name: {"handlers": ["default"], "level": normalized_level, "propagate": False} for name in logger_names
        },
    }


def configure_logging(
    *,
    level: str = "INFO",
    log_format: LogFormat = LogFormat.pretty,
    logger_names: Sequence[str] = (),
) -> None:
    """Configure logging once from an executable boundary."""
    logging.config.dictConfig(_logging_config(level=level, log_format=log_format, logger_names=logger_names))


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced standard-library logger without configuring it."""
    return logging.getLogger(name)
