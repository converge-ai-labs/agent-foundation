"""Keep the real MCP Apps launcher isolated from daily-use state and credentials."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from a13n_harness_ui.settings_loader import load_harness_ui_settings

launcher = importlib.import_module("dev.harness-ui.mcp_apps")
cli_module = importlib.import_module("a13n_harness_ui.cli")


@pytest.mark.parametrize("outcome", ["return", "exit", "error", "interrupt"])
def test_demo_configuration_isolation_and_cleanup(tmp_path: Path, monkeypatch, outcome: str) -> None:
    example = tmp_path / "example"
    (example / "assets").mkdir(parents=True)
    (example / "assets/app.html").write_text("built App")
    monkeypatch.setattr(launcher, "EXAMPLE", example)
    original_home = tmp_path / "daily home"
    original_home.mkdir()
    marker = original_home / "keep.txt"
    marker.write_text("daily state")
    monkeypatch.setenv("HOME", str(original_home))
    monkeypatch.setenv("A13N_HARNESS_UI_API_KEY", "fictional-daily-key")
    monkeypatch.setenv("A13N_HARNESS_UI_DATA_ROOT", str(original_home))
    monkeypatch.setattr(sys, "argv", ["mcp_apps", "--port", "9000"])
    previous_environment = dict(os.environ)
    previous_directory = Path.cwd()
    roots: list[Path] = []
    model_lifetime: list[str] = []

    @contextmanager
    def model_process(*, port: int):
        assert port == 0
        model_lifetime.append("started")
        try:
            yield "http://127.0.0.1:12345/v1"
        finally:
            model_lifetime.append("stopped")

    def run(*, args: list[str], prog_name: str) -> None:
        root = Path.home().parent
        roots.append(root)
        assert Path.home() != original_home
        assert Path.cwd() == root / "workspace"
        assert "A13N_HARNESS_UI_API_KEY" not in os.environ
        assert os.environ["MCP_APPS_DEMO_KEY"] == "local-scripted-not-a-secret"
        config = root / "config/a13n-harness-ui.yaml"
        loaded = asyncio.run(load_harness_ui_settings(config))
        assert loaded.candidate_error is None
        assert loaded.configuration is not None
        document = loaded.configuration.document
        assert document.defaults.agent == "agent-counter"
        assert document.webui.mcp_apps.enabled
        assert document.webui.mcp_apps.servers == ("mcp-counter",)
        agent = loaded.configuration.agents["agent-counter"]
        assert loaded.configuration.selected_mcp_servers(agent) == ()
        assert agent.capabilities[0].configuration == {"rules": {"mcp/mcp-counter/reset_counter": "ask"}}
        assert prog_name == "a13n-harness-ui"
        assert args == [
            "--no-update-check",
            "--config",
            str(config),
            "--data-root",
            str(root / "data"),
            "webui",
            "--no-share-computer",
            "--port",
            "9000",
        ]
        if outcome == "exit":
            raise SystemExit(7)
        if outcome == "error":
            raise RuntimeError("startup failed")
        if outcome == "interrupt":
            raise KeyboardInterrupt

    monkeypatch.setattr(launcher, "model_process", model_process)
    monkeypatch.setattr(cli_module.cli, "main", run)
    if outcome == "return":
        launcher.main()
    else:
        error = {"exit": SystemExit, "error": RuntimeError, "interrupt": KeyboardInterrupt}[outcome]
        with pytest.raises(error):
            launcher.main()
    assert model_lifetime == ["started", "stopped"]
    assert len(roots) == 1 and not roots[0].exists()
    assert Path.cwd() == previous_directory
    assert dict(os.environ) == previous_environment
    assert marker.read_text() == "daily state"
    assert list(original_home.iterdir()) == [marker]


def test_demo_requires_prepared_assets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(launcher, "EXAMPLE", tmp_path)
    with pytest.raises(SystemExit, match="make mcp-apps-example-assets"):
        launcher.main()
