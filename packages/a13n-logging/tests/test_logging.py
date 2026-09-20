import json
import logging
import time

from a13n_logging import (
    BoundedRotatingFileHandler,
    ContextFilter,
    JsonFormatter,
    LogDestination,
    LogFormat,
    PrettyFormatter,
    build_logging_config,
    configure_logging,
)
from rich.logging import RichHandler


def _record() -> logging.LogRecord:
    record = logging.LogRecord(
        name="a13n.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="session_started",
        args=(),
        exc_info=None,
    )
    record.session_id = "session-1"
    return record


def test_json_formatter_preserves_structured_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record()))

    assert payload["message"] == "session_started"
    assert payload["session_id"] == "session-1"


def test_pretty_formatter_preserves_event_context() -> None:
    output = PrettyFormatter().format(_record())

    assert output == 'a13n.test session_started session_id="session-1"'


def test_context_filter_adds_defaults_without_replacing_call_site_fields() -> None:
    record = logging.makeLogRecord({"msg": "ready", "role": "execution"})

    ContextFilter({"service": "a13n-service", "role": "control"}).filter(record)

    assert record.service == "a13n-service"
    assert record.role == "execution"


def test_logging_config_selects_pretty_and_json_handlers() -> None:
    pretty = build_logging_config(log_format=LogFormat.pretty, logger_names=["a13n"])
    json_config = build_logging_config(log_format=LogFormat.json, logger_names=["a13n"])

    assert pretty["handlers"]["default"]["()"] is RichHandler
    assert json_config["handlers"]["default"]["class"] == "logging.StreamHandler"


def test_logging_config_supports_file_and_both_destinations(tmp_path) -> None:
    path = tmp_path / "service.log"
    file_only = build_logging_config(
        log_format=LogFormat.json,
        logger_names=["a13n"],
        destination=LogDestination.file,
        file_path=str(path),
    )
    both = build_logging_config(
        logger_names=["a13n"],
        destination=LogDestination.both,
        file_path=str(path),
    )

    assert file_only["loggers"]["a13n"]["handlers"] == ["file"]
    assert both["loggers"]["a13n"]["handlers"] == ["default", "file"]


def test_file_handler_rotates_with_bounded_backups_and_closes(tmp_path) -> None:
    path = tmp_path / "service.log"
    handler = BoundedRotatingFileHandler(
        str(path), max_bytes=80, backup_count=2, queue_capacity=100, shutdown_timeout_seconds=2
    )
    handler.setFormatter(JsonFormatter())
    for index in range(20):
        handler.emit(logging.makeLogRecord({"msg": f"record-{index}-with-padding"}))
    handler.close()

    files = sorted(tmp_path.glob("service.log*"))
    assert path in files
    assert len(files) == 3
    assert not handler._thread.is_alive()


def test_file_handler_flushes_each_background_write_before_shutdown(tmp_path) -> None:
    path = tmp_path / "service.log"
    handler = BoundedRotatingFileHandler(
        str(path), max_bytes=1024, backup_count=1, queue_capacity=10, shutdown_timeout_seconds=2
    )
    handler.setFormatter(JsonFormatter())
    handler.emit(logging.makeLogRecord({"msg": "visible_while_open"}))
    handler._queue.join()

    assert "visible_while_open" in path.read_text()
    assert handler._thread.is_alive()
    handler.close()


def test_file_handler_drops_instead_of_blocking_when_queue_is_full(tmp_path, monkeypatch) -> None:
    handler = BoundedRotatingFileHandler(
        str(tmp_path / "service.log"),
        max_bytes=1024,
        backup_count=1,
        queue_capacity=1,
        shutdown_timeout_seconds=2,
    )
    entered = __import__("threading").Event()
    release = __import__("threading").Event()
    original = logging.handlers.RotatingFileHandler.emit

    def slow_emit(self, record):
        entered.set()
        release.wait(1)
        original(self, record)

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, "emit", slow_emit)
    handler.emit(logging.makeLogRecord({"msg": "first"}))
    assert entered.wait(1)
    handler.emit(logging.makeLogRecord({"msg": "queued"}))
    started = time.monotonic()
    handler.emit(logging.makeLogRecord({"msg": "dropped"}))
    assert time.monotonic() - started < 0.1
    assert handler.dropped_records == 1
    release.set()
    handler.close()
    assert handler.dropped_records == 1


def test_logging_shutdown_drains_file_and_reconfiguration_starts_one_new_writer(tmp_path) -> None:
    path = tmp_path / "service.log"
    configure_logging(
        logger_names=["a13n.test.shutdown"],
        log_format=LogFormat.json,
        destination=LogDestination.file,
        file_path=str(path),
    )
    first = logging.getLogger("a13n.test.shutdown").handlers[0]
    logging.getLogger("a13n.test.shutdown").info("before_shutdown")
    logging.shutdown()
    assert isinstance(first, BoundedRotatingFileHandler)
    assert not first._thread.is_alive()
    assert "before_shutdown" in path.read_text()

    configure_logging(
        logger_names=["a13n.test.shutdown"],
        log_format=LogFormat.json,
        destination=LogDestination.file,
        file_path=str(path),
    )
    second = logging.getLogger("a13n.test.shutdown").handlers[0]
    assert isinstance(second, BoundedRotatingFileHandler)
    assert second is not first and second._thread.is_alive()
    logging.getLogger("a13n.test.shutdown").info("after_reconfigure")
    logging.shutdown()
    assert not second._thread.is_alive()
    assert "after_reconfigure" in path.read_text()


def test_both_destinations_write_real_records(tmp_path, capsys) -> None:
    path = tmp_path / "both.log"
    configure_logging(
        logger_names=["a13n.test.both"],
        log_format=LogFormat.json,
        destination=LogDestination.both,
        file_path=str(path),
    )
    logger = logging.getLogger("a13n.test.both")
    logger.info("both_outputs")
    for handler in logger.handlers:
        handler.close()
    assert json.loads(capsys.readouterr().out)["message"] == "both_outputs"
    assert json.loads(path.read_text())["message"] == "both_outputs"


def test_disk_failure_does_not_escape_or_print_record(tmp_path, capsys) -> None:
    handler = BoundedRotatingFileHandler(
        str(tmp_path),
        max_bytes=1024,
        backup_count=1,
    )
    handler.emit(logging.makeLogRecord({"msg": "private_payload"}))
    handler.close()
    assert handler.failed_records == 1
    assert "private_payload" not in capsys.readouterr().err
