from __future__ import annotations

import subprocess

import pytest

from scripts.prepare_harness_ui_image import PACKAGES, prepare


def test_release_context_copies_exact_wheel_and_no_source_dependencies(tmp_path, monkeypatch) -> None:
    release = tmp_path / "release"
    release.mkdir()
    wheel = release / "a13n_harness_ui-1.2.3rc1-py3-none-any.whl"
    wheel.write_bytes(b"exact published artifact")
    (release / "a13n_harness_ui-1.2.3rc1.tar.gz").write_bytes(b"sdist")
    target = tmp_path / "context"
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="anyio==4.14.2\n")

    monkeypatch.setattr(subprocess, "run", run)
    prepare(target, release)
    assert (target / wheel.name).read_bytes() == wheel.read_bytes()
    assert {item.name for item in target.iterdir()} == {wheel.name, "constraints.txt"}
    assert len(commands) == 1 and commands[0][1] == "export"
    assert "--locked" in commands[0] and "--no-emit-workspace" in commands[0]


def test_source_context_builds_only_ui_and_its_workspace_dependency_closure(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    built = []

    def run(command, **kwargs):
        if command[1] == "build":
            package = command[command.index("--package") + 1]
            built.append(package)
            (Path(command[-1]) / f"{package}.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(command, 0, stdout="anyio==4.14.2\n")

    monkeypatch.setattr(subprocess, "run", run)
    target = tmp_path / "context"
    prepare(target)
    assert tuple(built) == PACKAGES
    assert "a13n-service" not in built
    assert len(list(target.glob("*.whl"))) == 6


def test_missing_or_ambiguous_release_wheel_keeps_previous_context(tmp_path) -> None:
    target = tmp_path / "context"
    target.mkdir()
    (target / "keep").write_text("previous build")
    with pytest.raises(ValueError, match="exactly one"):
        prepare(target, tmp_path)
    assert (target / "keep").read_text() == "previous build"
