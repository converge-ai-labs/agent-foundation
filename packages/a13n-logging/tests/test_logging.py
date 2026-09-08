import json
import logging

from a13n_logging import (
    ContextFilter,
    JsonFormatter,
    LogFormat,
    PrettyFormatter,
    build_logging_config,
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
