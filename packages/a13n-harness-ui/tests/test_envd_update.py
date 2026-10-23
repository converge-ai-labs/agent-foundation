"""Explicit Envd maintenance stays outside the application lifecycle."""

from pathlib import Path

import pytest
from a13n_harness_ui import envd_update, managed_runtime, terminal
from a13n_harness_ui.cli import cli
from a13n_harness_ui.errors import RuntimeResolutionError
from click.testing import CliRunner


def _config(tmp_path: Path, executable: Path | None = None) -> Path:
    config = tmp_path / "a13n-harness-ui.yaml"
    config.write_text("{}\n" if executable is None else f"envd:\n  executable: {executable}\n")
    return config


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("check", [False, True])
def test_standalone_delegates_without_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool, check: bool
) -> None:
    executable = tmp_path / "a13n-envd"
    executable.touch()
    config = _config(tmp_path, executable if configured else None)
    calls = []

    def unexpected(*args, **kwargs):
        pytest.fail("Envd update must not start the terminal or managed runtime")

    async def supports(path):
        assert path == executable
        return True

    async def native(path, **kwargs):
        calls.append((path, kwargs))
        return "native result\n"

    monkeypatch.setattr(terminal, "start", unexpected)
    monkeypatch.setattr(envd_update, "ManagedEnvdRuntime", unexpected)
    monkeypatch.setattr(envd_update, "supports_native_update", supports)
    monkeypatch.setattr(envd_update, "run_native_update", native)
    args = ["--config", str(config), "envd", "update", "--version", "1.2.3"]
    if not configured:
        args += ["--executable", str(executable)]
    args += ["--check" if check else "--force"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert calls == [(executable, {"version": "1.2.3", "check": check, "force": not check})]
    assert "native result" in result.output
    assert not (tmp_path / "data").exists()


def test_managed_check_uses_client_version_and_selected_data_root_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    root = tmp_path / "selected-data"
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda name: "1.2.3rc1")

    async def unexpected(*args, **kwargs):
        pytest.fail("Managed --check must not execute or download")

    monkeypatch.setattr(managed_runtime.ManagedEnvdRuntime, "resolve", unexpected)
    result = CliRunner().invoke(cli, ["--config", str(config), "--data-root", str(root), "envd", "update", "--check"])
    assert result.exit_code == 0, result.output
    assert "Managed Envd: 1.2.3-rc.1" in result.output
    assert str(root / "runtimes" / "managed" / "1.2.3-rc.1") in result.output
    assert "Not installed" in result.output
    assert not root.exists()


@pytest.mark.parametrize("force", [False, True])
def test_managed_acquisition_and_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force: bool) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda name: "1.2.3")
    calls = []

    async def resolve(self, *, reinstall=False):
        calls.append(reinstall)
        return tmp_path / "managed" / "a13n-envd"

    monkeypatch.setattr(managed_runtime.ManagedEnvdRuntime, "resolve", resolve)
    args = ["--config", str(config), "envd", "update"] + (["--force"] if force else [])
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert calls == [force]
    assert "Envd ready" in result.output


@pytest.mark.parametrize(
    "args,message",
    [(["--check", "--force"], "cannot be combined"), (["--version", "9.0.0"], "Managed Envd requires 1.2.3")],
)
def test_invalid_selections_fail_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], message: str
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(managed_runtime.metadata, "version", lambda name: "1.2.3")
    result = CliRunner().invoke(cli, ["--config", str(config), "envd", "update", *args])
    assert result.exit_code == 2, result.output
    assert message in result.output
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "error,code",
    [
        (RuntimeResolutionError("native failure", code="envd_update_failed", details={"returncode": 7}), 7),
        (KeyboardInterrupt(), 130),
        (FileNotFoundError("missing executable"), 1),
    ],
)
def test_cli_reports_failure_without_retry(monkeypatch: pytest.MonkeyPatch, error: BaseException, code: int) -> None:
    calls = []

    async def update(**kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(envd_update, "update", update)
    result = CliRunner().invoke(cli, ["envd", "update"])
    assert result.exit_code == code, result.output
    assert len(calls) == 1
    assert "Envd ready" not in result.output


def test_legacy_standalone_requires_explicit_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "a13n-envd"
    executable.touch()
    config = _config(tmp_path, executable)

    async def legacy(path):
        return False

    monkeypatch.setattr(envd_update, "supports_native_update", legacy)
    result = CliRunner().invoke(cli, ["--config", str(config), "envd", "update"])
    assert result.exit_code == 1
    assert "standalone Envd installer" in result.output


def test_missing_config_is_not_silently_replaced(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["--config", str(tmp_path / "missing.yaml"), "envd", "update"])
    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "missing.yaml").exists()
