from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import a13n_ui.cli as cli_module
import pytest
from a13n_harness.model_auth import CodexCredentials
from a13n_ui.cli import main
from a13n_ui.errors import ConfigurationError
from a13n_ui.storage import ObjectKind, ObjectRef


def test_defaults_to_interactive_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main([])

    assert len(calls) == 1
    assert calls[0].command is None


def test_account_login_parser_exposes_only_the_wired_codex_flow() -> None:
    parser = cli_module._parser()

    parsed = parser.parse_args(["account", "login", "codex"])
    assert parsed.provider == "codex"

    with pytest.raises(SystemExit):
        parser.parse_args(["account", "login", "grok"])


@pytest.mark.anyio
async def test_codex_cli_login_uses_harness_oauth_flow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials = CodexCredentials(
        account_id="account-1",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        access_token="access-secret",
        refresh_token="refresh-secret",
    )

    class Flow:
        def authorization_url(self) -> str:
            return "https://auth.example/authorize"

        async def exchange_code_from_callback(self) -> CodexCredentials:
            return credentials

    monkeypatch.setattr(cli_module, "CodexOAuthFlow", Flow)

    result = await cli_module._codex_cli_login(object())

    assert result is credentials
    assert "https://auth.example/authorize" in capsys.readouterr().err


def test_parses_management_commands_and_data_root() -> None:
    parser = cli_module._parser()

    validate = parser.parse_args(["--data-root", "/tmp/a13n-data", "config", "validate", "--format", "json"])
    import_subagents = parser.parse_args(
        [
            "config",
            "import-subagents",
            "--product",
            "codex",
            "--scope",
            "project",
            "--project-root",
            "/tmp/project",
            "--apply",
        ]
    )
    thread = parser.parse_args(["thread", "archive", "thread-1", "--restore"])

    assert validate.data_root.as_posix() == "/tmp/a13n-data"
    assert validate.config_command == "validate"
    assert validate.format == "json"
    assert import_subagents.product == "codex"
    assert import_subagents.scope == "project"
    assert import_subagents.apply is True
    assert thread.thread_id == "thread-1"
    assert thread.restore is True


def test_parses_headless_thread_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main(
        [
            "run",
            "inspect plugin",
            "--project",
            "project-main",
            "--agent",
            "agent-assistant",
            "--environment-profile",
            "environment-native",
            "--format",
            "json",
        ]
    )

    args = calls[0]
    assert args.command == "run"
    assert args.prompt == "inspect plugin"
    assert args.project == "project-main"
    assert args.agent == "agent-assistant"
    assert args.environment_profile == "environment-native"
    assert args.format == "json"


@pytest.mark.anyio
async def test_headless_run_creates_thread_from_defaults_and_prints_text(capsys: pytest.CaptureFixture[str]) -> None:
    app: Any = _FakeApp(_completed_outcome("plugin checked"))
    args = cli_module._parser().parse_args(["run", "check plugin"])

    exit_code = await cli_module._run_one_shot(app, _configuration(), args)

    assert exit_code == 0
    assert app.created == [("project-main", "agent-assistant", None, None)]
    assert app.runs == [("thread-new", "check plugin")]
    assert capsys.readouterr().out == "plugin checked\n"


@pytest.mark.anyio
async def test_headless_run_continues_thread_and_emits_structured_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = SimpleNamespace(code="model_failed", message="provider unavailable", retry_hint="safe")
    app: Any = _FakeApp(_failed_outcome(failure))
    args = cli_module._parser().parse_args(["run", "retry", "--thread", "thread-existing", "--format", "json"])

    exit_code = await cli_module._run_one_shot(app, _configuration(), args)

    assert exit_code == 1
    assert app.created == []
    assert app.runs == [("thread-existing", "retry")]
    payload = json.loads(capsys.readouterr().out)
    assert payload["thread_id"] == "thread-existing"
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "failed"
    assert payload["failure"] == {
        "code": "model_failed",
        "message": "provider unavailable",
        "retry_hint": "safe",
    }
    assert payload["continuation"] == {"status": "not_available", "reference": None}
    assert payload["environment"] == {"cleanup_error_count": 0, "state_publications": {}}
    assert payload["composition"]["object_kind"] == "run-composition"


@pytest.mark.anyio
async def test_headless_run_requires_project_and_agent_defaults() -> None:
    app: Any = _FakeApp(_completed_outcome("unused"))
    args = cli_module._parser().parse_args(["run", "check"])

    with pytest.raises(ConfigurationError) as missing:
        await cli_module._run_one_shot(app, _configuration(project=None), args)

    assert missing.value.code == "run_project_required"
    assert app.runs == []


@pytest.mark.anyio
async def test_headless_run_rejects_creation_arguments_for_existing_thread() -> None:
    app: Any = _FakeApp(_completed_outcome("unused"))
    args = cli_module._parser().parse_args(
        ["run", "check", "--thread", "thread-existing", "--agent", "agent-assistant"]
    )

    with pytest.raises(ConfigurationError) as conflict:
        await cli_module._run_one_shot(app, _configuration(), args)

    assert conflict.value.code == "run_arguments_conflict"
    assert app.runs == []


class _FakeApp:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.created: list[tuple[str | None, str | None, str | None, str | None]] = []
        self.runs: list[tuple[str, str]] = []

    async def create_thread(self, *, defaults: Any, title: str | None) -> Any:
        self.created.append((defaults.project_id, defaults.agent_id, defaults.environment_profile_id, title))
        return SimpleNamespace(thread_id="thread-new")

    async def run_thread(self, *, thread_id: str, prompt: str) -> Any:
        self.runs.append((thread_id, prompt))
        return self._outcome


def _configuration(*, project: str | None = "project-main", agent: str | None = "agent-assistant") -> Any:
    projects = {} if project is None else {project: object()}
    agents = {} if agent is None else {agent: object()}
    return SimpleNamespace(
        document=SimpleNamespace(
            defaults=SimpleNamespace(
                project=project,
                agent=agent,
                environment_profile=None,
            )
        ),
        projects=projects,
        agents=agents,
    )


def _composition() -> ObjectRef:
    return ObjectRef(
        object_kind=ObjectKind.run_composition,
        object_schema_version="1",
        logical_digest="1" * 64,
    )


def _completed_outcome(output: str) -> Any:
    return SimpleNamespace(
        result=SimpleNamespace(
            run_id="run-1",
            status="completed",
            output=output,
            failure=None,
            suspend_reason=None,
        ),
        composition=_composition(),
        continuation=SimpleNamespace(status="selected", reference=None),
        environment=SimpleNamespace(cleanup_errors=(), state_publications=()),
    )


def _failed_outcome(failure: Any) -> Any:
    return SimpleNamespace(
        result=SimpleNamespace(
            run_id="run-1",
            status="failed",
            output=None,
            failure=failure,
            suspend_reason=None,
        ),
        composition=_composition(),
        continuation=SimpleNamespace(status="not_available", reference=None),
        environment=SimpleNamespace(cleanup_errors=(), state_publications=()),
    )
