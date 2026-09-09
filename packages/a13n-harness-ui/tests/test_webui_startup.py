from __future__ import annotations

from contextlib import asynccontextmanager

import a13n_harness_ui.cli as cli_module
import a13n_harness_ui.webui as webui
import pytest
from a13n_harness_ui.cli import cli
from a13n_harness_ui.errors import HarnessUiError
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A13N_HARNESS_UI_API_KEY", raising=False)


@asynccontextmanager
async def unopened():
    raise AssertionError("Startup validation must not open the App")
    yield  # pragma: no cover


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["cli", "environment", "both"])
async def test_key_precedence_and_no_supplied_secret_in_output(source, monkeypatch, capsys) -> None:
    configs = []

    async def serve(self):
        configs.append(self.config)

    monkeypatch.setattr(webui.uvicorn.Server, "serve", serve)
    if source in {"environment", "both"}:
        monkeypatch.setenv("A13N_HARNESS_UI_API_KEY", "environment-secret")
    await webui.run(unopened, api_key="cli-secret" if source in {"cli", "both"} else None)
    boundary = next(item for item in configs[0].app.user_middleware if item.cls is webui.AccessBoundary)
    assert boundary.kwargs["api_key"] == ("environment-secret" if source == "environment" else "cli-secret")
    captured = capsys.readouterr()
    assert "WebUI" in captured.out
    assert "cli-secret" not in captured.out + captured.err
    assert "environment-secret" not in captured.out + captured.err
    assert "#api_key=" not in captured.out + captured.err


@pytest.mark.anyio
async def test_generated_keys_rotate_and_only_startup_stdout_contains_them(monkeypatch, capsys) -> None:
    async def serve(self):
        pass

    monkeypatch.setattr(webui.uvicorn.Server, "serve", serve)
    keys = []
    for _ in range(2):
        await webui.run(unopened, host="0.0.0.0")
        captured = capsys.readouterr()
        key = captured.out.split("API key: ")[1].splitlines()[0]
        keys.append(key)
        assert len(key) >= 40
        assert f"http://127.0.0.1:8765/#api_key={key}" in captured.out
        assert key not in captured.err
    assert keys[0] != keys[1]


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["cli", "environment"])
@pytest.mark.parametrize("value", ["", "configured-key"])
async def test_skip_rejects_any_supplied_key(source, value, monkeypatch) -> None:
    if source == "environment":
        monkeypatch.setenv("A13N_HARNESS_UI_API_KEY", value)
    with pytest.raises(HarnessUiError) as error:
        await webui.run(unopened, api_key=value if source == "cli" else None, dangerously_bypass_permission=True)
    assert error.value.code == "webui_access_conflict"


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["cli", "environment"])
async def test_explicit_empty_key_is_not_replaced_with_generated_key(source, monkeypatch) -> None:
    if source == "environment":
        monkeypatch.setenv("A13N_HARNESS_UI_API_KEY", "")
    with pytest.raises(HarnessUiError) as error:
        await webui.run(unopened, api_key="" if source == "cli" else None)
    assert error.value.code == "webui_key_invalid"


@pytest.mark.parametrize("flag", ["--apikey", "--api-key"])
def test_key_aliases_dispatch_the_same_input(flag, monkeypatch) -> None:
    requests = []
    monkeypatch.setattr(cli_module, "_execute", requests.append)
    result = CliRunner().invoke(cli, ["webui", flag, "selected"])
    assert result.exit_code == 0, result.output
    assert requests[0].web_api_key == "selected"


@pytest.mark.parametrize("flag", ["--dangerous-skip-permissions", "--dangerously-bypass-permission"])
def test_skip_aliases_dispatch_the_same_input(flag, monkeypatch) -> None:
    requests = []
    monkeypatch.setattr(cli_module, "_execute", requests.append)
    result = CliRunner().invoke(cli, ["webui", flag])
    assert result.exit_code == 0, result.output
    assert requests[0].dangerously_bypass_permission


def test_conflicting_alias_values_fail_without_echoing_keys() -> None:
    result = CliRunner().invoke(cli, ["webui", "--apikey", "first-secret", "--api-key", "second-secret"])
    assert result.exit_code == 2
    assert "Conflicting" in result.output
    assert "first-secret" not in result.output and "second-secret" not in result.output
