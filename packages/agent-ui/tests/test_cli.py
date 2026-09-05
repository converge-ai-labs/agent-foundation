from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import a13n_ui.cli as cli_module
import pytest
from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from a13n_ui.cli import CliRequest, OutputFormat, cli, main
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
from click.testing import CliRunner


def test_defaults_to_interactive_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[CliRequest] = []

    async def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(cli_module, "_run", run)
    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0].command is None


def test_tui_is_the_default_and_canonical_interactive_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[CliRequest] = []

    async def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(cli_module, "_run", run)
    runner = CliRunner()
    arguments = [
        "--project",
        "project-main",
        "--agent",
        "agent-reviewer",
        "--environment-mode",
        "sandbox",
    ]

    bare = runner.invoke(cli, arguments)
    explicit = runner.invoke(cli, ["tui", *arguments])
    split = runner.invoke(cli, [*arguments, "tui"])
    webui = runner.invoke(cli, ["webui"])

    assert bare.exit_code == explicit.exit_code == split.exit_code == webui.exit_code == 0
    assert calls[0].command is None
    assert calls[1].command == "tui"
    assert calls[2].command == "tui"
    assert calls[3].command == "webui"
    launches = [cli_module._tui_launch_options(request) for request in calls[:3]]
    assert launches[0] == launches[1] == launches[2]
    launch = launches[0]
    assert launch.thread_id is None
    assert launch.defaults.project_id == "project-main"
    assert launch.defaults.agent_id == "agent-reviewer"
    assert launch.defaults.environment_profile_id == "environment-sandbox"


def test_tui_rejects_new_thread_overrides_when_opening_existing_thread() -> None:
    request = CliRequest(command="tui", thread_id="thread-1", agent_id="agent-reviewer")

    with pytest.raises(ConfigurationError) as conflict:
        cli_module._tui_launch_options(request)

    assert conflict.value.code == "tui_arguments_conflict"


def test_click_help_lists_complete_command_tree_without_running_app(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected_run(request: CliRequest) -> int:
        del request
        raise AssertionError("help must not start the application")

    monkeypatch.setattr(cli_module, "_run", unexpected_run)
    runner = CliRunner()

    root = runner.invoke(cli, ["--help"])
    thread = runner.invoke(cli, ["thread", "--help"])
    auth = runner.invoke(cli, ["auth", "login", "--help"])

    assert root.exit_code == thread.exit_code == auth.exit_code == 0
    for command in ("tui", "webui", "run", "config", "import", "plugin", "thread", "doctor", "auth"):
        assert command in root.output
    assert "list" in thread.output
    assert "show" in thread.output
    assert "archive" in thread.output
    assert "--allow-account-switch" in auth.output
    assert "--device-code" in auth.output


def test_environment_list_exposes_release_owned_modes(tmp_path: Path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text('schema_version: "2"\n')

    # Exercise the real application and JSON boundary without another cold
    # interpreter startup; test_entrypoint covers the executable boundary.
    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(settings),
            "--data-root",
            str(tmp_path / "state"),
            "environment",
            "list",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    profiles = json.loads(result.stdout)["environment_profiles"]
    assert {profile["profile_id"]: profile["mode"] for profile in profiles} == {
        "environment-native": "full-control",
        "environment-sandbox": "sandbox",
    }


def test_click_reports_invalid_and_conflicting_options_as_usage_errors() -> None:
    runner = CliRunner()

    invalid_provider = runner.invoke(cli, ["auth", "login", "unsupported"])
    conflicting_environment = runner.invoke(
        cli,
        ["run", "inspect", "--environment-mode", "sandbox", "--environment-profile", "custom"],
    )
    invalid_limit = runner.invoke(cli, ["thread", "list", "--limit", "0"])

    assert invalid_provider.exit_code == 2
    assert "Invalid value" in invalid_provider.output
    assert "unsupported" in invalid_provider.output
    assert conflicting_environment.exit_code == 2
    assert "cannot be combined" in conflicting_environment.output
    assert invalid_limit.exit_code == 2
    assert "0 is not in the range x>=1" in invalid_limit.output


def test_agent_ui_errors_have_stable_text_and_json_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(request: CliRequest) -> int:
        del request
        raise ConfigurationError("Configuration is unavailable.", code="configuration_unavailable")

    monkeypatch.setattr(cli_module, "_run", fail)
    runner = CliRunner()

    text = runner.invoke(cli, ["run", "inspect"])
    structured = runner.invoke(cli, ["run", "inspect", "--format", "json"])

    assert text.exit_code == structured.exit_code == 1
    assert text.stdout == ""
    assert text.stderr == "Error [configuration_unavailable]: Configuration is unavailable.\n"
    assert json.loads(structured.stdout) == {
        "error": {"code": "configuration_unavailable", "message": "Configuration is unavailable."}
    }
    assert structured.stderr == ""


def test_auth_login_interruption_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(request: CliRequest) -> int:
        del request
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "_run", run)

    result = CliRunner().invoke(cli, ["auth", "login", "grok"])

    assert result.exit_code == 130
    assert result.output == ""


def test_click_builds_typed_management_requests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[CliRequest] = []

    async def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(cli_module, "_run", run)
    runner = CliRunner()
    data_root = tmp_path / "data"

    results = [
        runner.invoke(cli, ["--data-root", str(data_root), "config", "validate", "--format", "json"]),
        runner.invoke(
            cli,
            [
                "import",
                "subagents",
                "--product",
                "codex",
                "--scope",
                "project",
                "--project-root",
                str(tmp_path),
                "--apply",
            ],
        ),
        runner.invoke(
            cli,
            [
                "plugin",
                "install",
                "https://example.com/plugins.git",
                "--plugin",
                "plugin-reviewer",
                "--ref",
                "v1",
            ],
        ),
        runner.invoke(cli, ["thread", "archive", "thread-1", "--expected-version", "3", "--restore"]),
    ]

    assert all(result.exit_code == 0 for result in results)
    validate, import_subagents, plugin, thread = calls
    assert validate == CliRequest(
        command="config",
        action="validate",
        data_root=data_root,
        output_format=OutputFormat.json,
    )
    assert import_subagents.command == "import"
    assert import_subagents.action == "subagents"
    assert import_subagents.product == "codex"
    assert import_subagents.scope == "project"
    assert import_subagents.project_root == tmp_path
    assert import_subagents.apply is True
    assert plugin.repository == "https://example.com/plugins.git"
    assert plugin.plugin_id == "plugin-reviewer"
    assert plugin.ref == "v1"
    assert thread.thread_id == "thread-1"
    assert thread.restore is True
    assert thread.expected_version == 3


def test_click_builds_typed_headless_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[CliRequest] = []

    async def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(cli_module, "_run", run)
    result = CliRunner().invoke(
        cli,
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        CliRequest(
            command="run",
            prompt="inspect plugin",
            project_id="project-main",
            agent_id="agent-assistant",
            environment_profile_id="environment-native",
            output_format=OutputFormat.json,
        )
    ]


def test_main_wrapper_returns_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[CliRequest] = []

    async def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(cli_module, "_run", run)

    assert main(["webui"]) is None
    assert calls[0].command == "webui"


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


def test_builtin_environment_mode_resolves_to_stable_profile_id() -> None:
    request = CliRequest(
        command="run",
        prompt="inspect plugin",
        project_id="project-main",
        agent_id="agent-assistant",
        environment_mode="sandbox",
    )

    defaults = cli_module._new_thread_defaults(_configuration(), request)

    assert defaults.environment_profile_id == "environment-sandbox"


@pytest.mark.anyio
async def test_headless_run_creates_thread_from_defaults_and_prints_text(capsys: pytest.CaptureFixture[str]) -> None:
    app: Any = _FakeApp(_completed_outcome("plugin checked"))
    request = CliRequest(command="run", prompt="check plugin")

    exit_code = await cli_module._run_one_shot(app, _configuration(), request)

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
    request = CliRequest(
        command="run",
        prompt="retry",
        thread_id="thread-existing",
        output_format=OutputFormat.json,
    )

    exit_code = await cli_module._run_one_shot(app, _configuration(), request)

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
    request = CliRequest(command="run", prompt="check")

    with pytest.raises(ConfigurationError) as missing:
        await cli_module._run_one_shot(app, _configuration(project=None), request)

    assert missing.value.code == "run_project_required"
    assert app.runs == []


@pytest.mark.anyio
async def test_headless_run_rejects_creation_arguments_for_existing_thread() -> None:
    app: Any = _FakeApp(_completed_outcome("unused"))
    request = CliRequest(
        command="run",
        prompt="check",
        thread_id="thread-existing",
        agent_id="agent-assistant",
    )

    with pytest.raises(ConfigurationError) as conflict:
        await cli_module._run_one_shot(app, _configuration(), request)

    assert conflict.value.code == "run_arguments_conflict"
    assert app.runs == []


def test_text_renderer_is_readable_and_stable() -> None:
    rendered = cli_module._render_text(
        {
            "plugins": [
                {
                    "plugin_id": "plugin-reviewer",
                    "enabled": True,
                    "description": None,
                }
            ],
            "next_cursor": None,
            "warnings": [],
        }
    )

    assert rendered == (
        "Plugins (1):\n"
        "  - Plugin ID: plugin-reviewer\n"
        "    Enabled: yes\n"
        "    Description: -\n"
        "Next cursor: -\n"
        "Warnings (0): -"
    )


def test_projection_json_remains_compact_and_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    cli_module._print_projection(
        {"message": "检查完成", "items": [1, 2], "empty": None},
        OutputFormat.json,
    )

    assert capsys.readouterr().out == '{"message":"检查完成","items":[1,2],"empty":null}\n'


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
