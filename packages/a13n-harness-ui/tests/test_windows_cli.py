"""Windows local execution is explicit Full Control, never an implicit downgrade."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_harness_ui import environment_profiles
from a13n_harness_ui.cli import CliRequest, cli
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.rendering import Status
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.setup import preflight_environment
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def windows_local_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch only the policy module, not Python's global platform/path behavior.
    monkeypatch.setattr(environment_profiles, "sys", SimpleNamespace(platform="win32"))


@pytest.mark.parametrize(
    "arguments",
    [
        ["--environment-mode", "sandbox"],
        ["run", "hello", "--environment-mode", "sandbox"],
        ["--environment-profile", "environment-sandbox"],
    ],
)
def test_windows_rejects_explicit_sandbox_before_startup(arguments: list[str]) -> None:
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 2
    assert "Windows supports Full Control only" in result.output
    assert "no automatic fallback" in result.output


def test_windows_setup_offers_only_explicit_full_control() -> None:
    wizard = SetupWizard()
    while wizard.question.key != "environment":
        wizard.accept("")
    assert wizard.question.choices == ("full-control",)
    assert "Windows Sandbox is not available" in wizard.prompt()
    with pytest.raises(ValueError):
        wizard.accept("sandbox")
    assert "environment" not in wizard.values
    wizard.accept("full-control")
    assert wizard.values["environment"] == "full-control"


@pytest.mark.anyio
async def test_windows_preflight_does_not_download_or_probe(tmp_path: Path) -> None:
    async def resolve() -> Path:
        pytest.fail("Windows Sandbox must not acquire envd")

    blocked = await preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve)
    assert not blocked.ready
    assert blocked.code == "windows_sandbox_unavailable"
    native = await preflight_environment("environment-native", tmp_path, resolve_executable=resolve)
    assert native.ready
    assert "not a sandbox" in native.message


@pytest.mark.anyio
@pytest.mark.parametrize("thread_id", [None, "thread-saved"])
async def test_windows_blocks_saved_sandbox_before_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thread_id: str | None
) -> None:
    from unittest.mock import AsyncMock

    status = Status(environment="environment-sandbox")
    backend = SessionBackend(None, CliRequest(thread_id=thread_id), tmp_path, status)
    monkeypatch.setattr(backend, "refresh", AsyncMock(return_value=True))
    with pytest.raises(ValueError, match="Windows supports Full Control only"):
        await backend.ensure_session()
    assert backend.thread_id == thread_id
    assert status.environment == "environment-sandbox"


@pytest.mark.anyio
async def test_windows_selector_rejects_sandbox_without_changing_saved_selection(tmp_path: Path) -> None:
    # Rejection must precede every App read/write; an unavailable App proves that.
    backend = SessionBackend(None, CliRequest(), tmp_path, Status())
    backend.thread_id = "thread-existing"
    backend.environment = "environment-sandbox"
    choices = await backend.choices("environment")
    assert [choice.value for choice in choices] == ["full-control"]
    with pytest.raises(ValueError, match="Windows supports Full Control only"):
        await backend.set_environment("sandbox")
    assert backend.environment == "environment-sandbox"
    assert backend.thread_id == "thread-existing"
