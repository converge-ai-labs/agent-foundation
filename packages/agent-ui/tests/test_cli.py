from __future__ import annotations

import argparse

import a13n_ui.cli as cli_module
from a13n_ui.cli import main


def test_defaults_to_interactive_cli(monkeypatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main([])

    assert len(calls) == 1
    assert calls[0].command is None


def test_accepts_explicit_tui_command(monkeypatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main(["tui"])

    assert len(calls) == 1
    assert calls[0].command == "tui"


def test_dispatches_runtime_status_with_json(monkeypatch) -> None:
    calls: list[argparse.Namespace] = []

    async def run(args: argparse.Namespace) -> None:
        calls.append(args)

    monkeypatch.setattr(cli_module, "_run", run)
    main(["--config", "/tmp/settings.yaml", "runtime", "status", "--json"])

    assert calls[0].config.as_posix() == "/tmp/settings.yaml"
    assert calls[0].command == "runtime"
    assert calls[0].runtime_command == "status"
    assert calls[0].json is True
