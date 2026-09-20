"""Process-boundary logging configuration with pretty and JSON output."""

from __future__ import annotations

import json
import logging
import logging.config
import logging.handlers
import queue
import threading
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
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


class LogDestination(StrEnum):
    """Supported process log destinations."""

    stdout = "stdout"
    file = "file"
    both = "both"


_bound_context: ContextVar[Mapping[str, object] | None] = ContextVar("a13n_logging_context", default=None)


@contextmanager
def bind_log_context(**fields: object) -> Generator[None]:
    """Bind task-local fields and restore the previous context on exit."""

    token = _bound_context.set({**(_bound_context.get() or {}), **fields})
    try:
        yield
    finally:
        _bound_context.reset(token)


def set_log_context(**fields: object) -> None:
    """Add fields to the current task context until its owner restores it."""

    _bound_context.set({**(_bound_context.get() or {}), **fields})


class ContextFilter(logging.Filter):
    """Attach stable process context without replacing call-site fields."""

    def __init__(self, fields: Mapping[str, object] | None = None) -> None:
        super().__init__()
        self._fields = dict(fields or {})

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in {**self._fields, **(_bound_context.get() or {})}.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class BoundedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Write through a bounded queue to a standard size-rotating handler."""

    def __init__(
        self,
        filename: str,
        *,
        max_bytes: int,
        backup_count: int,
        queue_capacity: int = 4096,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        if (
            max_bytes <= 0
            or backup_count <= 0
            or queue_capacity <= 0
            or not isfinite(shutdown_timeout_seconds)
            or shutdown_timeout_seconds <= 0
        ):
            raise ValueError("rotating file logging bounds are invalid")
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        self._queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=queue_capacity)
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._write_records, name="a13n-log-file", daemon=True)
        self._thread.start()
        self.dropped_records = 0
        self.failed_records = 0

    def emit(self, record: logging.LogRecord) -> None:
        snapshot = logging.LogRecord(
            name=record.name,
            level=record.levelno,
            pathname="",
            lineno=0,
            msg=self.format(record),
            args=(),
            exc_info=None,
        )
        snapshot._a13n_preformatted = snapshot.msg
        if self._stop.is_set():
            self.dropped_records += 1
            return
        try:
            self._queue.put_nowait(snapshot)
        except queue.Full:
            self.dropped_records += 1

    def format(self, record: logging.LogRecord) -> str:
        snapshot = getattr(record, "_a13n_preformatted", None)
        return snapshot if isinstance(snapshot, str) else super().format(record)

    def handleError(self, record: logging.LogRecord) -> None:
        del record
        self.failed_records += 1

    def _write_records(self) -> None:
        try:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    record = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    super().emit(record)
                    if self.stream is not None:
                        self.stream.flush()
                except Exception:
                    self.failed_records += 1
                finally:
                    self._queue.task_done()
        finally:
            try:
                if self.stream is not None:
                    self.stream.flush()
                    self.stream.close()
                    self.stream = None
            except Exception:
                self.failed_records += 1

    def flush(self) -> None:
        """File flushing belongs to the writer thread, including at shutdown."""

    def close(self) -> None:
        if not self._stop.is_set():
            self._stop.set()
            self._thread.join(self._shutdown_timeout_seconds)
        logging.Handler.close(self)


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


def build_logging_config(
    *,
    level: str = "INFO",
    log_format: LogFormat = LogFormat.pretty,
    logger_names: Sequence[str] = (),
    context: Mapping[str, object] | None = None,
    destination: LogDestination = LogDestination.stdout,
    file_path: str | None = None,
    file_max_bytes: int = 10 * 1024 * 1024,
    file_backup_count: int = 5,
) -> dict[str, Any]:
    """Build a dictConfig shared by service and worker executables."""
    normalized_level = level.upper()
    formatter = log_format.value
    filters: dict[str, Any] = {
        "context": {
            "()": "a13n_logging.ContextFilter",
            "fields": dict(context or {}),
        }
    }
    handler_filters = ["context"]

    if destination in {LogDestination.file, LogDestination.both} and not file_path:
        raise ValueError("file_path is required when file logging is enabled")

    handlers: dict[str, Any] = {}
    selected_handlers: list[str] = []
    if destination in {LogDestination.stdout, LogDestination.both} and log_format is LogFormat.pretty:
        handlers["default"] = {
            "()": RichHandler,
            "formatter": formatter,
            "filters": handler_filters,
            "level": normalized_level,
            "markup": False,
            "rich_tracebacks": True,
            "show_path": False,
        }
        selected_handlers.append("default")
    elif destination in {LogDestination.stdout, LogDestination.both}:
        handlers["default"] = {
            "class": "logging.StreamHandler",
            "formatter": formatter,
            "filters": handler_filters,
            "level": normalized_level,
            "stream": "ext://sys.stdout",
        }
        selected_handlers.append("default")
    if destination in {LogDestination.file, LogDestination.both}:
        handlers["file"] = {
            "()": "a13n_logging.BoundedRotatingFileHandler",
            "filename": file_path,
            "max_bytes": file_max_bytes,
            "backup_count": file_backup_count,
            "formatter": formatter,
            "filters": handler_filters,
            "level": normalized_level,
        }
        selected_handlers.append("file")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "a13n_logging.JsonFormatter"},
            "pretty": {"()": "a13n_logging.PrettyFormatter"},
        },
        "filters": filters,
        "handlers": handlers,
        "loggers": {
            name: {
                "handlers": selected_handlers,
                "level": normalized_level,
                "propagate": False,
            }
            for name in logger_names
        },
    }


def configure_logging(
    *,
    level: str = "INFO",
    log_format: LogFormat = LogFormat.pretty,
    logger_names: Sequence[str] = (),
    context: Mapping[str, object] | None = None,
    destination: LogDestination = LogDestination.stdout,
    file_path: str | None = None,
    file_max_bytes: int = 10 * 1024 * 1024,
    file_backup_count: int = 5,
) -> None:
    """Configure logging once from an executable boundary."""
    logging.config.dictConfig(
        build_logging_config(
            level=level,
            log_format=log_format,
            logger_names=logger_names,
            context=context,
            destination=destination,
            file_path=file_path,
            file_max_bytes=file_max_bytes,
            file_backup_count=file_backup_count,
        )
    )


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced standard-library logger without configuring it."""
    return logging.getLogger(name)
