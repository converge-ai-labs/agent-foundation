"""Configuration for the disposable manual Console review."""

import os
from pathlib import Path

import pytest
from a13n_service.settings import load_settings

from dev.service.console_review import service_config


def test_console_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("A13N_")]:
        monkeypatch.delenv(name)
    console = tmp_path / "console.toml"
    console.write_text(
        service_config(
            tmp_path,
            "postgresql+psycopg://e2e:fixture@127.0.0.1:5432/console",
            "redis://127.0.0.1:6379/0",
            console="http://localhost:5173",
            workspace_id="ws_console",
            origins=("http://127.0.0.1:18080",),
        )
    )
    assert load_settings(console).server.public_url == "http://localhost:5173"
