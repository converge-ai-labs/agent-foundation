"""Process-boundary logging configuration: pretty or JSON stdout, a rotating JSON file, or both."""

from __future__ import annotations

import json
import logging
import logging.config
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
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


@dataclass(frozen=True)
class LogFile:
    """A size-rotated JSON log file: when the active file would pass `max_bytes`, it becomes `<path>.1`, older
    backups shift up, and the oldest beyond `backups` is deleted. The active file is not counted in `backups`.

    One process owns one file; processes sharing a path would rotate it under each other.
    """

    path: Path
    max_bytes: int
    backups: int

    def __post_init__(self) -> None:
        # Standard logging never rotates when either value is zero, so the file would grow without bound.
        if self.max_bytes < 1 or self.backups < 1:
            raise ValueError("A log file needs max_bytes and backups of at least 1")


def _logging_config(
    *, level: str, log_format: LogFormat, logger_names: Sequence[str], stdout: bool, file: LogFile | None
) -> dict[str, Any]:
    normalized_level = level.upper()
    handlers: dict[str, dict[str, Any]] = {}
    if stdout:
        handlers["stdout"] = (
            {"()": RichHandler, "markup": False, "rich_tracebacks": True, "show_path": False}
            if log_format is LogFormat.pretty
            else {"class": "logging.StreamHandler", "stream": "ext://sys.stdout"}
        ) | {"formatter": log_format.value}
    if file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(file.path),
            "maxBytes": file.max_bytes,
            "backupCount": file.backups,
            "encoding": "utf-8",
            "formatter": LogFormat.json.value,
        }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "a13n_logging.JsonFormatter"},
            "pretty": {"()": "a13n_logging.PrettyFormatter"},
        },
        "filters": {"context": {"()": "a13n_logging.context.ContextFilter"}},
        "handlers": {
            name: {**handler, "level": normalized_level, "filters": ["context"]} for name, handler in handlers.items()
        },
        "loggers": {
            name: {"handlers": list(handlers), "level": normalized_level, "propagate": False} for name in logger_names
        },
    }


def configure_logging(
    *,
    level: str = "INFO",
    log_format: LogFormat = LogFormat.pretty,
    logger_names: Sequence[str] = (),
    stdout: bool = True,
    file: LogFile | None = None,
) -> None:
    """Configure logging once from an executable boundary.

    Records go to stdout in `log_format`, to a rotating JSON `file`, or to both; every record carries the fields
    bound by `log_context`.
    """
    if not stdout and file is None:
        raise ValueError("Logging needs stdout, a file, or both")
    logging.config.dictConfig(
        _logging_config(level=level, log_format=log_format, logger_names=logger_names, stdout=stdout, file=file)
    )


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced standard-library logger without configuring it."""
    return logging.getLogger(name)
