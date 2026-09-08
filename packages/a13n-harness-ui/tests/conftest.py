from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic_ai import prices


@pytest.fixture(autouse=True)
def isolated_user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))


@pytest.fixture(autouse=True)
def no_background_price_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "update_in_background", nullcontext)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
