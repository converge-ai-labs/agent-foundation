import asyncio
import json
import logging
from pathlib import Path

import pytest
from a13n_logging import JsonFormatter, LogFile, LogFormat, PrettyFormatter, configure_logging, log_context
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


def test_context_fields_reach_every_record_until_the_block_exits(capsys) -> None:
    configure_logging(logger_names=["a13n.test.context"], log_format=LogFormat.json)
    logger = logging.getLogger("a13n.test.context.child")

    with log_context(request_id="req-1", run_id="run-1"):
        with log_context(run_id="run-2"):
            logger.info("inner")
        logger.info("outer", extra={"request_id": "explicit"})
    logger.info("after")

    inner, outer, after = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert (inner["request_id"], inner["run_id"]) == ("req-1", "run-2")
    assert (outer["request_id"], outer["run_id"]) == ("explicit", "run-1")
    assert "request_id" not in after and "run_id" not in after


def test_concurrent_tasks_never_see_each_others_context(capsys) -> None:
    configure_logging(logger_names=["a13n.test.tasks"], log_format=LogFormat.json)
    logger = logging.getLogger("a13n.test.tasks")

    async def interleave() -> None:
        first_bound, second_logged = asyncio.Event(), asyncio.Event()

        async def first() -> None:
            with log_context(request_id="req-first"):
                first_bound.set()
                await second_logged.wait()
                logger.info("first")

        async def second() -> None:
            await first_bound.wait()
            with log_context(request_id="req-second"):
                logger.info("second")
            second_logged.set()

        async with asyncio.TaskGroup() as group:
            group.create_task(first())
            group.create_task(second())

    asyncio.run(interleave())

    records = {record["message"]: record for record in map(json.loads, capsys.readouterr().out.splitlines())}
    assert records["first"]["request_id"] == "req-first"
    assert records["second"]["request_id"] == "req-second"


def test_file_output_rotates_and_keeps_the_configured_backups(tmp_path: Path, capsys) -> None:
    path = tmp_path / "service.log"
    configure_logging(
        logger_names=["a13n.test.file"],
        log_format=LogFormat.pretty,
        stdout=False,
        file=LogFile(path=path, max_bytes=2000, backups=2),
    )
    logger = logging.getLogger("a13n.test.file")

    with log_context(run_id="run-1"):
        for index in range(200):
            logger.info("line", extra={"index": index, "padding": "x" * 40})
    for handler in logger.handlers:
        handler.close()

    assert sorted(item.name for item in tmp_path.iterdir()) == ["service.log", "service.log.1", "service.log.2"]
    last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert (last["message"], last["index"], last["run_id"]) == ("line", 199, "run-1")
    assert capsys.readouterr().out == ""


def test_both_outputs_receive_each_record(tmp_path: Path, capsys) -> None:
    path = tmp_path / "service.log"
    configure_logging(
        logger_names=["a13n.test.both"], log_format=LogFormat.json, file=LogFile(path=path, max_bytes=10_000, backups=1)
    )
    logger = logging.getLogger("a13n.test.both")

    logger.info("ready")
    for handler in logger.handlers:
        handler.close()

    assert json.loads(capsys.readouterr().out)["message"] == "ready"
    assert json.loads(path.read_text(encoding="utf-8"))["message"] == "ready"


def test_outputs_that_would_never_rotate_or_write_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        LogFile(path=tmp_path / "service.log", max_bytes=1_000, backups=0)
    with pytest.raises(ValueError, match="stdout, a file, or both"):
        configure_logging(logger_names=["a13n.test.none"], stdout=False)
