import json
import logging

from a13n_logging import JsonFormatter, LogFormat, PrettyFormatter, configure_logging
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


def test_configure_logging_selects_pretty_and_json_handlers() -> None:
    configure_logging(log_format=LogFormat.pretty, logger_names=["a13n.test.pretty"])
    configure_logging(log_format=LogFormat.json, logger_names=["a13n.test.json"])

    [pretty] = logging.getLogger("a13n.test.pretty").handlers
    [json_handler] = logging.getLogger("a13n.test.json").handlers
    assert isinstance(pretty, RichHandler)
    assert type(json_handler) is logging.StreamHandler
    assert isinstance(json_handler.formatter, JsonFormatter)


def test_json_output_writes_one_record_to_stdout(capsys) -> None:
    configure_logging(logger_names=["a13n.test.stdout"], log_format=LogFormat.json)

    logging.getLogger("a13n.test.stdout").info("ready", extra={"task_id": "task-1"})

    payload = json.loads(capsys.readouterr().out)
    assert payload["message"] == "ready"
    assert payload["task_id"] == "task-1"
    assert not logging.getLogger("a13n.test.stdout").propagate
