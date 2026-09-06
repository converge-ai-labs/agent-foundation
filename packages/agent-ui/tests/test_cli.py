from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import a13n_ui.cli as cli_module
import a13n_ui.cli_runtime as runtime_module
import a13n_ui.terminal as terminal_module
import pytest
from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from a13n_ui.cli import CliRequest, OutputFormat, cli, main
from a13n_ui.errors import ConfigurationError
from a13n_ui.model_accounts import DEFAULT_GROK_OAUTH_SCOPE, GrokLoginRequest
from click.testing import CliRunner


def test_defaults_to_interactive_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[CliRequest] = []

    def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(terminal_module, "start", run)
    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0].command is None


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
    removed = runner.invoke(cli, ["thread", "list"])

    assert invalid_provider.exit_code == 2
    assert "Invalid value" in invalid_provider.output
    assert "unsupported" in invalid_provider.output
    assert conflicting_environment.exit_code == 2
    assert "cannot be combined" in conflicting_environment.output
    assert removed.exit_code == 2
    assert "No such command" in removed.output


def test_agent_ui_errors_have_stable_text_and_json_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(request: CliRequest) -> int:
        del request
        raise ConfigurationError("Configuration is unavailable.", code="configuration_unavailable")

    monkeypatch.setattr(runtime_module, "_run", fail)
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

    monkeypatch.setattr(runtime_module, "_run", run)

    result = CliRunner().invoke(cli, ["auth", "login", "grok"])

    assert result.exit_code == 130
    assert result.output == ""


def test_click_builds_typed_management_requests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[CliRequest] = []

    async def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(runtime_module, "_run", run)
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
    ]

    assert all(result.exit_code == 0 for result in results)
    validate, import_subagents, plugin = calls
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


def test_click_builds_typed_headless_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[CliRequest] = []

    async def run(request: CliRequest) -> int:
        calls.append(request)
        return 0

    monkeypatch.setattr(runtime_module, "_run", run)
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "inspect plugin",
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

    monkeypatch.setattr(runtime_module, "_run", run)

    assert main(["doctor"]) is None
    assert calls[0].command == "doctor"


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

        async def exchange_code_from_callback(self, *, timeout_seconds: float) -> CodexCredentials:
            assert timeout_seconds == 900
            return credentials

    monkeypatch.setattr("a13n_ui.model_accounts.login.CodexOAuthFlow", Flow)

    from a13n_ui.model_accounts.codex import CodexLoginRequest

    result = await runtime_module._codex_cli_login(CodexLoginRequest(replacing_shared_account=False), device_code=False)

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
            assert timeout_seconds == 900
            return credentials

    monkeypatch.setattr("a13n_ui.model_accounts.login.GrokOAuthFlow", Flow)

    result = await runtime_module._grok_cli_login(
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
        expires_in = 600

        async def wait_for_credentials(self) -> GrokCredentials:
            return credentials

    class DeviceFlow:
        @classmethod
        async def start(cls, **kwargs: object) -> Authorization:
            assert kwargs["issuer"] == "https://issuer.example"
            assert kwargs["client_id"] == "client-id"
            return Authorization()

    monkeypatch.setattr("a13n_ui.model_accounts.login.GrokDeviceAuthorizationFlow", DeviceFlow)

    result = await runtime_module._grok_cli_login(
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


def test_text_renderer_is_readable_and_stable() -> None:
    rendered = runtime_module._render_text(
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
    runtime_module._print_projection(
        {"message": "检查完成", "items": [1, 2], "empty": None},
        OutputFormat.json,
    )

    assert capsys.readouterr().out == '{"message":"检查完成","items":[1,2],"empty":null}\n'


def test_cli_key_input_is_hidden_and_login_defaults_to_device(monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    requests = []
    monkeypatch.setattr(cli_module, "_execute", requests.append)
    runner = CliRunner()
    response = runner.invoke(cli_module.cli, ["auth", "key", "set", "key-test"], input="secret-command-key\n")
    assert response.exit_code == 0, response.output
    assert "secret-command-key" not in response.output
    assert "secret-command-key" not in repr(requests[0])
    assert requests[0].credential_key.get_secret_value() == "secret-command-key"
    assert runner.invoke(cli_module.cli, ["auth", "login", "codex"]).exit_code == 0
    assert requests[-1].device_code is True
    assert runner.invoke(cli_module.cli, ["auth", "login", "grok", "--browser"]).exit_code == 0
    assert requests[-1].device_code is False


def test_help_and_public_surface_are_cli_only() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for removed in ("webui", "thread", "project"):
        assert removed not in cli.commands
    assert "--resume" in result.output
    assert "--display" in result.output
    assert "--config" in result.output
