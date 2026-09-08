"""Terminal diagnostics, update detection, and resume hints remain bounded."""

import asyncio
import json
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive import lifecycle
from a13n_harness_ui.interactive.lifecycle import resume_hint, terminal_logging
from a13n_harness_ui.interactive.updates import AvailableUpdate, available_update, check_update


def test_versions_use_pep440_and_ignore_preview() -> None:
    assert available_update("1.9", "1.10")
    assert available_update("1.9", "1.10rc1") is None
    assert available_update("1.10", "1.9") is None
    assert available_update("bad", "1.9") is None


@pytest.mark.anyio
async def test_daily_update_cache_requires_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache" / "terminal-update.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"latest": "2.0", "checked_at": time.time()}))

    def no_client(*args: object, **kwargs: object) -> None:
        pytest.fail("fresh cache must not contact PyPI")

    monkeypatch.setattr(httpx2, "AsyncClient", no_client)
    assert await check_update(tmp_path, current="1.0") == AvailableUpdate("1.0", "2.0")


@pytest.mark.anyio
async def test_terminal_logs_are_files_and_plugin_diagnostics_are_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notices: list[str] = []
    logger = logging.getLogger("a13n_harness_ui.lifecycle_test")
    with terminal_logging(tmp_path, "INFO", notices.append) as path:
        for _ in range(2):
            logger.warning("content_plugin_skipped", extra={"path": "/plugins/installed", "reason": "legacy"})
        logger.info("ordinary diagnostic")
        await asyncio.sleep(0)
    assert len(notices) == 1
    assert "ordinary diagnostic" in path.read_text()
    assert "\x1b" not in path.read_text()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def _assert_resume_command(hint: str, arguments: list[str], *, platform: str = os.name) -> None:
    command = hint.splitlines()[1].removeprefix("  ")
    if platform == "nt":
        # shlex is a POSIX parser: it consumes unquoted Windows path backslashes.
        assert command == subprocess.list2cmdline(arguments)
    else:
        assert shlex.split(command) == arguments


def test_resume_hint_windows_quoting_on_every_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Replace only this module's OS view; changing os.name globally breaks pathlib.
    monkeypatch.setattr(lifecycle, "os", SimpleNamespace(name="nt", environ={}))
    request = CliRequest(config_path=tmp_path / "a b.yaml", data_root=tmp_path / "plain")
    _assert_resume_command(
        resume_hint(request, "thread_123", tmp_path),
        [
            "a13n-harness-ui",
            "--config",
            str(request.config_path),
            "--data-root",
            str(request.data_root),
            "--resume",
            "thread_123",
        ],
        platform="nt",
    )


def test_resume_hint_keeps_explicit_roots(tmp_path: Path) -> None:
    request = CliRequest(config_path=tmp_path / "a b.yaml", data_root=tmp_path / "data files")
    hint = resume_hint(request, "thread_123", tmp_path)
    _assert_resume_command(
        hint,
        [
            "a13n-harness-ui",
            "--config",
            str(request.config_path),
            "--data-root",
            str(request.data_root),
            "--resume",
            "thread_123",
        ],
    )


@pytest.mark.anyio
@pytest.mark.parametrize("behavior", ["success", "offline", "oversize", "invalid"])
async def test_update_checker_is_bounded_public_and_failure_tolerant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behavior: str
) -> None:
    original = httpx2.AsyncClient
    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.host == "pypi.org"
        assert "authorization" not in request.headers
        if behavior == "offline":
            raise httpx2.ConnectError("offline")
        if behavior == "oversize":
            return httpx2.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        if behavior == "invalid":
            return httpx2.Response(200, json={"info": {"version": "not a version"}})
        return httpx2.Response(200, json={"info": {"version": "2.0"}})

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] == 2
        return original(transport=httpx2.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    notice = await check_update(tmp_path, current="1.0")
    assert len(requests) == 1
    assert bool(notice) == (behavior == "success")
    assert (tmp_path / "cache" / "terminal-update.json").exists() == (behavior == "success")


@pytest.mark.parametrize("directory_name", ["plain", "with spaces"])
def test_resume_hint_preserves_environment_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory_name: str
) -> None:
    data_root = tmp_path / directory_name
    monkeypatch.setenv("A13N_HARNESS_UI_DATA_ROOT", str(data_root))
    _assert_resume_command(
        resume_hint(CliRequest(), "thread_1", tmp_path),
        ["a13n-harness-ui", "--data-root", str(data_root), "--resume", "thread_1"],
    )


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", ["gc", "gc-thread", "exception", "message", "missing-task", "task-exception"])
async def test_terminal_loop_failures_preserve_only_pending_task_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    import gc

    from a13n_harness_ui.interactive.shell import CliShell
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    async def empty():
        return None

    async def abandoned():
        await asyncio.Future()

    backend = SimpleNamespace(
        thread_id=None, resumed_transcript=None, interaction=empty, skill_catalog=empty, cancel=empty
    )
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    warning = scenario in {"gc", "gc-thread"}
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        notices: list[str] = []
        emit = shell.emit

        def capture(text, **kwargs):
            assert asyncio.get_running_loop() is loop
            notices.append(text)
            emit(text, **kwargs)

        monkeypatch.setattr(shell, "emit", capture)
        chat = asyncio.create_task(shell.run(backend))
        task = None
        try:
            async with asyncio.timeout(3):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
                shell.composer.text = "unsent draft"
                if warning:
                    task = asyncio.create_task(abandoned(), name="lost-test-task")
                    await asyncio.sleep(0)
                    del task
                    task = None
                    if scenario == "gc-thread":
                        await asyncio.to_thread(gc.collect)
                    else:
                        gc.collect()
                else:
                    context: dict[str, object] = {"message": "Task was destroyed but it is pending!"}
                    if scenario == "message":
                        context["message"] = "unrecognized event loop failure"
                    if scenario in {"exception", "task-exception"}:
                        context["exception"] = RuntimeError("private exception detail")
                    if scenario == "task-exception":
                        task = asyncio.create_task(abandoned())
                        context["task"] = task
                    loop.call_exception_handler(context)
                if warning:
                    while not any("terminal remains open" in text for text in notices):
                        await asyncio.sleep(0.01)
                    assert not chat.done()
                    assert shell.composer.text == "unsent draft"
                    assert shell.status.state == "ready"
                    assert shell.job is None
                    assert "No work was retried" in notices[-1]
                    shell.composer.text = ""
                    pipe.send_text("/quit\r")
                    await chat
                else:
                    with pytest.raises(RuntimeError, match="Unexpected RuntimeError"):
                        await chat
            data = json.loads(next(tmp_path.glob("a13n-harness-ui-error-*.json")).read_text())
            assert data["phase"] == "terminal event loop"
            assert "event_loop" in data
            if warning:
                assert data["event_loop"]["task_name"] == "lost-test-task"
                assert data["event_loop"]["coroutine"]["function"].endswith("abandoned")
            assert all("private exception detail" not in notice for notice in notices)
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if not chat.done():
                chat.cancel()
            await asyncio.gather(chat, return_exceptions=True)
    assert loop.get_exception_handler() is previous_handler
