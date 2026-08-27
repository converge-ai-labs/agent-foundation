from __future__ import annotations

from a13n_ui import tui, webui
from a13n_ui.cli import main


def test_defaults_to_webui(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(webui, "run", lambda: calls.append("webui"))

    main([])

    assert calls == ["webui"]


def test_dispatches_explicit_webui(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(webui, "run", lambda: calls.append("webui"))

    main(["webui"])

    assert calls == ["webui"]


def test_dispatches_tui(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tui, "run", lambda: calls.append("tui"))

    main(["tui"])

    assert calls == ["tui"]
