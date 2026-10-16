"""Windows Sandbox selection is available; real readiness never downgrades it."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui import environment_profiles
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.rendering import Status
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.setup import preflight_environment


@pytest.fixture(autouse=True)
def windows_local_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(environment_profiles, "sys", SimpleNamespace(platform="win32"))


def test_windows_setup_offers_native_sandbox() -> None:
    assert environment_profiles.local_sandbox_supported()
    environment_profiles.require_supported_local_profile("environment-sandbox")
    wizard = SetupWizard()
    while wizard.question.key != "environment":
        wizard.accept("")
    assert "sandbox" in wizard.question.choices
    wizard.accept("sandbox")
    assert wizard.values["environment"] == "sandbox"


@pytest.mark.anyio
async def test_windows_preflight_requires_production_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = AsyncMock(return_value=tmp_path / "a13n-envd.exe")
    probe = AsyncMock(side_effect=OSError("native Windows MXC process isolation is unavailable"))
    monkeypatch.setattr("a13n_harness_ui.setup.validate_sandbox_runtime", probe)
    blocked = await preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve)
    assert not blocked.ready and blocked.code == "sandbox_probe_failed"
    resolve.assert_awaited_once()
    probe.assert_awaited_once()
    assert blocked.profile_id == "environment-sandbox"
    probe.side_effect = None
    assert (await preflight_environment("environment-sandbox", tmp_path, resolve_executable=resolve)).ready
    resolve.reset_mock()
    native = await preflight_environment("environment-native", tmp_path, resolve_executable=resolve)
    assert native.ready and "not a sandbox" in native.message
    resolve.assert_not_awaited()


@pytest.mark.anyio
async def test_windows_selector_keeps_both_explicit_modes(tmp_path: Path) -> None:
    backend = SessionBackend(None, CliRequest(), tmp_path, Status())
    backend.thread_id = "thread-existing"
    backend.environment = "environment-sandbox"
    choices = await backend.choices("environment")
    assert [choice.value for choice in choices] == ["full-control", "envd", "desktop", "sandbox"]
    assert backend.environment == "environment-sandbox"
