"""Verify disposable first-launch state and cleanup without real model accounts."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest
from a13n_harness_ui.model_accounts.codex import resolve_codex_policy
from a13n_harness_ui.settings_loader import default_harness_ui_settings_path, load_harness_ui_settings

landing = importlib.import_module("dev.harness-ui.landing")
cli_module = importlib.import_module("a13n_harness_ui.cli")


@pytest.mark.parametrize("interface", ["cli", "webui"])
@pytest.mark.parametrize("outcome", ["return", "exit", "error", "interrupt"])
def test_landing_isolates_first_run_and_cleans_up(tmp_path: Path, monkeypatch, interface, outcome) -> None:
    original_home = tmp_path / "daily home"
    original_home.mkdir()
    marker = original_home / "keep.txt"
    marker.write_text("daily state")
    for name in ("HOME", "USERPROFILE", "CODEX_HOME", "GROK_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(original_home))
    monkeypatch.setenv("GROK_AUTH_PATH", str(marker))
    monkeypatch.setenv("A13N_HARNESS_UI_DATA_ROOT", str(original_home))
    monkeypatch.setenv("OPENAI_API_KEY", "fictional-shell-key")
    previous_environment = dict(os.environ)
    previous_directory = Path.cwd()
    arguments = ["--port", "9000", "--no-share-computer"] if interface == "webui" else []
    monkeypatch.setattr(sys, "argv", ["landing", interface, *arguments])
    roots: list[Path] = []

    def run(*, args: list[str], prog_name: str) -> None:
        home = Path.home()
        root = home.parent
        roots.append(root)
        assert home != original_home
        assert Path.cwd() == root / "workspace"
        assert list(Path.cwd().iterdir()) == []
        assert not default_harness_ui_settings_path().exists()
        loaded = asyncio.run(load_harness_ui_settings())
        assert not loaded.explicit and not loaded.exists
        assert loaded.candidate_error is None
        assert loaded.configuration is None
        assert loaded.settings.storage.data_root == root / "data"
        assert Path(os.environ["CODEX_HOME"]) == home / ".codex"
        asyncio.run(resolve_codex_policy())  # Must allow first-time login, not fail on a missing explicit home.
        assert Path(os.environ["GROK_AUTH_PATH"]) == home / ".grok/auth.json"
        assert os.environ["OPENAI_API_KEY"] == "fictional-shell-key"
        assert prog_name == "a13n-harness-ui"
        assert args == [
            "--no-update-check",
            "--data-root",
            str(root / "data"),
            *(["webui", *arguments] if interface == "webui" else []),
        ]
        # Include writes made during setup in cleanup, not just initially empty directories.
        configuration = default_harness_ui_settings_path()
        configuration.parent.mkdir(parents=True)
        configuration.write_text('schema_version: "1"\n')
        loaded.settings.storage.data_root.mkdir()
        (loaded.settings.storage.data_root / "auth.json").write_text("fictional-key")
        if outcome == "exit":
            raise SystemExit(7)
        if outcome == "error":
            raise RuntimeError("startup failed")
        if outcome == "interrupt":
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.cli, "main", run)
    if outcome == "return":
        landing.main()
    else:
        error = {"exit": SystemExit, "error": RuntimeError, "interrupt": KeyboardInterrupt}[outcome]
        with pytest.raises(error):
            landing.main()
    assert len(roots) == 1 and not roots[0].exists()
    assert Path.cwd() == previous_directory
    assert dict(os.environ) == previous_environment
    assert marker.read_text() == "daily state"
    assert list(original_home.iterdir()) == [marker]


def test_cli_landing_rejects_path_overrides(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["landing", "cli", "--config", "daily.yaml"])
    with pytest.raises(SystemExit) as error:
        landing.main()
    assert error.value.code == 2
