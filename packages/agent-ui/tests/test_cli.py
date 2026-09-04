from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import a13n_ui.cli as cli_module
import pytest
from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from a13n_ui.cli import main
from a13n_ui.errors import ConfigurationError
from a13n_ui.model_accounts import DEFAULT_GROK_OAUTH_SCOPE, GrokLoginRequest
from a13n_ui.surfaces import (
    ContinuationSelectionView,
    EnvironmentOutcomeView,
    FailureView,
    RootExecutionView,
    RootOperationStatus,
    RootOperationView,
    RootRunOutcomeView,
    RootRunReceipt,
)


def test_defaults_to_interactive_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main([])

    assert len(calls) == 1
    assert calls[0].command is None


def test_tui_is_the_default_and_canonical_interactive_command() -> None:
    parser = cli_module._parser()

    bare = parser.parse_args(
        [
            "--project",
            "project-main",
            "--agent",
            "agent-reviewer",
            "--environment-mode",
            "sandbox",
        ]
    )
    explicit = parser.parse_args(
        [
            "tui",
            "--project",
            "project-main",
            "--agent",
            "agent-reviewer",
            "--environment-mode",
            "sandbox",
        ]
    )
    webui = parser.parse_args(["webui"])

    assert bare.command is None
    assert explicit.command == "tui"
    assert webui.command == "webui"
    assert cli_module._tui_launch_options(bare) == cli_module._tui_launch_options(explicit)
    launch = cli_module._tui_launch_options(bare)
    assert launch.thread_id is None
    assert launch.defaults.project_id == "project-main"
    assert launch.defaults.agent_id == "agent-reviewer"
    assert launch.defaults.environment_profile_id == "environment-sandbox"


def test_tui_rejects_new_thread_overrides_when_opening_existing_thread() -> None:
    args = cli_module._parser().parse_args(["tui", "--thread", "thread-1", "--agent", "agent-reviewer"])

    with pytest.raises(ConfigurationError) as conflict:
        cli_module._tui_launch_options(args)

    assert conflict.value.code == "tui_arguments_conflict"


def test_auth_login_interruption_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(args: argparse.Namespace) -> None:
        del args
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "_run", run)

    with pytest.raises(SystemExit) as interrupted:
        main(["auth", "login", "grok"])

    assert interrupted.value.code == 130


def test_auth_parser_exposes_codex_and_grok_native_flows() -> None:
    parser = cli_module._parser()

    codex = parser.parse_args(["auth", "login", "codex"])
    grok = parser.parse_args(["auth", "login", "grok", "--device-code"])
    status = parser.parse_args(["auth", "status"])

    assert codex.provider == "codex"
    assert codex.device_code is False
    assert grok.provider == "grok"
    assert grok.device_code is True
    assert status.provider is None


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


@pytest.mark.anyio
async def test_grok_cli_login_uses_native_browser_oauth_flow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials = GrokCredentials(
        account_id="account-1",
        auth_mode="oidc",
        create_time=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        issuer="https://auth.x.ai",
        client_id="b1a00492-073a-47ea-816f-4c329264a828",
        access_token="access-secret",
        refresh_token="refresh-secret",
    )
    discovered: list[dict[str, object]] = []

    class Flow:
        @classmethod
        async def discover(cls, **kwargs: object) -> Flow:
            discovered.append(kwargs)
            return cls()

        def authorization_url(self) -> str:
            return "https://auth.example/authorize"

        async def exchange_code_from_callback(self, *, timeout_seconds: float) -> GrokCredentials:
            assert timeout_seconds == 600
            return credentials

    monkeypatch.setattr(cli_module, "GrokOAuthFlow", Flow)

    result = await cli_module._grok_cli_login(
        GrokLoginRequest(scope=DEFAULT_GROK_OAUTH_SCOPE, replacing_shared_account=False),
        device_code=False,
    )

    assert result is credentials
    assert discovered[0]["issuer"] == "https://auth.x.ai"
    assert "grok-cli:access" in discovered[0]["scopes"]
    captured = capsys.readouterr()
    assert "https://auth.example/authorize" in captured.err
    assert "access-secret" not in captured.err
    assert captured.out == ""


@pytest.mark.anyio
async def test_grok_cli_login_uses_device_authorization_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials = GrokCredentials(
        account_id="account-1",
        auth_mode="oidc",
        create_time=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        issuer="https://issuer.example",
        client_id="client-id",
        access_token="access-secret",
        refresh_token="refresh-secret",
    )

    class Authorization:
        verification_uri = "https://issuer.example/device"
        verification_uri_complete = None
        user_code = "ABCD-1234"

        async def wait_for_credentials(self) -> GrokCredentials:
            return credentials

    class DeviceFlow:
        @classmethod
        async def start(cls, **kwargs: object) -> Authorization:
            assert kwargs["issuer"] == "https://issuer.example"
            assert kwargs["client_id"] == "client-id"
            return Authorization()

    monkeypatch.setattr(cli_module, "GrokDeviceAuthorizationFlow", DeviceFlow)

    result = await cli_module._grok_cli_login(
        GrokLoginRequest(
            scope="https://issuer.example::client-id",
            replacing_shared_account=False,
        ),
        device_code=True,
    )

    assert result is credentials
    captured = capsys.readouterr()
    assert "https://issuer.example/device" in captured.err
    assert "ABCD-1234" in captured.err
    assert "access-secret" not in captured.err
    assert captured.out == ""


def test_parses_management_commands_and_data_root() -> None:
    parser = cli_module._parser()

    validate = parser.parse_args(["--data-root", "/tmp/a13n-data", "config", "validate", "--format", "json"])
    import_subagents = parser.parse_args(
        [
            "import",
            "subagents",
            "--product",
            "codex",
            "--scope",
            "project",
            "--project-root",
            "/tmp/project",
            "--apply",
        ]
    )
    environment = parser.parse_args(["environment", "list", "--format", "json"])
    plugin = parser.parse_args(
        ["plugin", "install", "https://example.com/plugins.git", "--plugin", "plugin-reviewer", "--ref", "v1"]
    )
    thread = parser.parse_args(["thread", "archive", "thread-1", "--expected-version", "3", "--restore"])

    assert validate.data_root.as_posix() == "/tmp/a13n-data"
    assert validate.config_command == "validate"
    assert validate.format == "json"
    assert import_subagents.product == "codex"
    assert import_subagents.scope == "project"
    assert import_subagents.apply is True
    assert environment.environment_command == "list"
    assert environment.format == "json"
    assert plugin.plugin_command == "install"
    assert plugin.plugin_id == "plugin-reviewer"
    assert plugin.ref == "v1"
    assert thread.thread_id == "thread-1"
    assert thread.restore is True
    assert thread.expected_version == 3


def test_builtin_environment_mode_resolves_to_stable_profile_id() -> None:
    args = cli_module._parser().parse_args(
        [
            "run",
            "inspect plugin",
            "--project",
            "project-main",
            "--agent",
            "agent-assistant",
            "--environment-mode",
            "sandbox",
        ]
    )

    defaults = cli_module._new_thread_defaults(_configuration(), args)

    assert args.environment_mode == "sandbox"
    assert defaults.environment_profile_id == "environment-sandbox"


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
    assert payload["receipt"]["thread_id"] == "thread-existing"
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "failed"
    assert payload["outcome"]["execution"]["failure"] == {
        "code": "model_failed",
        "message": "provider unavailable",
        "details": None,
        "retry_hint": "safe",
    }
    assert payload["outcome"]["continuation"]["status"] == "not_available"
    assert payload["outcome"]["environment"]["cleanup_failures"] == []
    assert payload["outcome"]["composition_id"] == "1" * 64


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

    async def submit_thread(self, *, thread_id: str, prompt: str) -> RootRunReceipt:
        self.runs.append((thread_id, prompt))
        return self._outcome.receipt

    async def wait_root_operation(self, receipt_id: str) -> Any:
        assert receipt_id == self._outcome.receipt.receipt_id
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


def _receipt() -> RootRunReceipt:
    return RootRunReceipt(
        receipt_id="receipt-1",
        thread_id="thread-existing",
        submitted_at=datetime.now(UTC),
    )


def _completed_outcome(output: str) -> RootOperationView:
    receipt = _receipt().model_copy(update={"thread_id": "thread-new"})
    return RootOperationView(
        receipt=receipt,
        status=RootOperationStatus.completed,
        run_id="run-1",
        completed_at=datetime.now(UTC),
        outcome=RootRunOutcomeView(
            execution=RootExecutionView(status="completed", output=output),
            continuation=ContinuationSelectionView(status="selected", continuation_id="2" * 64),
            environment=EnvironmentOutcomeView(unchanged=0, published=0, failed=0),
            composition_id="1" * 64,
        ),
    )


def _failed_outcome(failure: Any) -> RootOperationView:
    return RootOperationView(
        receipt=_receipt(),
        status=RootOperationStatus.failed,
        run_id="run-1",
        completed_at=datetime.now(UTC),
        outcome=RootRunOutcomeView(
            execution=RootExecutionView(
                status="failed",
                failure=FailureView(
                    code=failure.code,
                    message=failure.message,
                    retry_hint=failure.retry_hint,
                ),
            ),
            continuation=ContinuationSelectionView(status="not_available"),
            environment=EnvironmentOutcomeView(unchanged=0, published=0, failed=0),
            composition_id="1" * 64,
        ),
    )
