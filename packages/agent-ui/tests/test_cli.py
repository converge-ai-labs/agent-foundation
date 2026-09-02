from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import a13n_ui.cli as cli_module
import pytest
from a13n_ui.cli import main
from a13n_ui.errors import ConfigurationError


def test_defaults_to_interactive_cli(monkeypatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main([])

    assert len(calls) == 1
    assert calls[0].command is None


def test_accepts_explicit_cli_command(monkeypatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main(["cli"])

    assert len(calls) == 1
    assert calls[0].command == "cli"


def test_parses_headless_run_arguments(monkeypatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main(
        [
            "run",
            "inspect plugin",
            "--agent",
            "assistant",
            "--environment",
            "native",
            "--folder",
            "first",
            "--folder",
            "second",
            "--format",
            "json",
        ]
    )

    assert len(calls) == 1
    args = calls[0]
    assert args.command == "run"
    assert args.prompt == "inspect plugin"
    assert args.agent == "assistant"
    assert args.environment == "native"
    assert args.folder == [Path("first"), Path("second")]
    assert args.format == "json"


@pytest.mark.anyio
async def test_headless_run_creates_session_from_defaults_and_uses_current_folder(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    app: Any = _FakeApp(_completed_outcome("plugin checked"))
    configuration: Any = _configuration()
    args = cli_module._parser().parse_args(["run", "check plugin"])

    exit_code = await cli_module._run_one_shot(app, configuration, args)

    assert exit_code == 0
    assert app.created == [("assistant", "native", None)]
    assert app.runs == [("session-new", "check plugin", (tmp_path,))]
    assert capsys.readouterr().out == "plugin checked\n"


@pytest.mark.anyio
async def test_headless_run_continues_session_and_emits_structured_failure(capsys) -> None:
    failure = SimpleNamespace(code="model_failed", message="provider unavailable", retry_hint="safe")
    app: Any = _FakeApp(_failed_outcome(failure))
    configuration: Any = _configuration()
    args = cli_module._parser().parse_args(["run", "retry", "--session", "session-existing", "--format", "json"])

    exit_code = await cli_module._run_one_shot(app, configuration, args)

    assert exit_code == 1
    assert app.created == []
    assert app.runs[0][0] == "session-existing"
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "session_id": "session-existing",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "status": "failed",
        "output": None,
        "output_truncated": False,
        "failure": {
            "code": "model_failed",
            "message": "provider unavailable",
            "retry_hint": "safe",
        },
        "suspend_reason": None,
        "continuation": {"status": "not_available", "reference": None},
        "environment": {"cleanup_error_count": 0, "state_publications": {}},
    }


@pytest.mark.anyio
async def test_headless_run_uses_implicit_native_and_fails_when_continuation_is_not_selected(
    capsys,
) -> None:
    outcome = _completed_outcome("finished but not retained")
    outcome.continuation.status = "failed"
    app: Any = _FakeApp(outcome)
    configuration: Any = _configuration(environment=None)
    args = cli_module._parser().parse_args(["run", "check"])

    exit_code = await cli_module._run_one_shot(app, configuration, args)

    assert exit_code == 1
    assert app.created == [("assistant", "__native__", None)]
    captured = capsys.readouterr()
    assert captured.out == "finished but not retained\n"
    assert "continuation was not selected: failed" in captured.err


@pytest.mark.anyio
async def test_headless_run_rejects_creation_arguments_for_existing_session() -> None:
    app: Any = _FakeApp(_completed_outcome("unused"))
    configuration: Any = _configuration()
    args = cli_module._parser().parse_args(["run", "check", "--session", "session-existing", "--agent", "assistant"])

    with pytest.raises(ConfigurationError) as conflict:
        await cli_module._run_one_shot(app, configuration, args)

    assert conflict.value.code == "run_arguments_conflict"
    assert app.runs == []


class _FakeApp:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.created: list[tuple[str, str, str | None]] = []
        self.runs: list[tuple[str, str, tuple[Path, ...]]] = []

    async def create_session(
        self,
        *,
        agent_name: str,
        environment_name: str,
        title: str | None,
    ) -> Any:
        self.created.append((agent_name, environment_name, title))
        return SimpleNamespace(session_id="session-new")

    async def run_session(
        self,
        *,
        session_id: str,
        prompt: str,
        folders: tuple[Path, ...],
    ) -> Any:
        self.runs.append((session_id, prompt, folders))
        return self._outcome


def _configuration(*, environment: str | None = "native") -> Any:
    return SimpleNamespace(
        document=SimpleNamespace(
            defaults=SimpleNamespace(agent="assistant", environment=environment),
            agents={"assistant": SimpleNamespace(environment=None)},
        )
    )


def _completed_outcome(output: str) -> Any:
    return SimpleNamespace(
        result=SimpleNamespace(
            thread_id="thread-1",
            run_id="run-1",
            status="completed",
            output=output,
            failure=None,
            suspend_reason=None,
        ),
        continuation=SimpleNamespace(status="selected", reference=None),
        environment=SimpleNamespace(cleanup_errors=(), state_publications=()),
    )


def _failed_outcome(failure: Any) -> Any:
    return SimpleNamespace(
        result=SimpleNamespace(
            thread_id="thread-1",
            run_id="run-1",
            status="failed",
            output=None,
            failure=failure,
            suspend_reason=None,
        ),
        continuation=SimpleNamespace(status="not_available", reference=None),
        environment=SimpleNamespace(cleanup_errors=(), state_publications=()),
    )
