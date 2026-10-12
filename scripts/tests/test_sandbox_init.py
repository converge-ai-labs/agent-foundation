from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
INIT = ROOT / "deploy/docker/images/sandbox/init-skills.sh"
ENTRYPOINT = ROOT / "deploy/docker/images/sandbox/entrypoint.sh"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux image scripts use GNU coreutils")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    source = tmp_path / "bundled"
    skill = source / "agent-browser"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: agent-browser\ndescription: Browser automation\n---\nGuide\n")
    (skill / "references").mkdir()
    (skill / "references/commands.md").write_text("commands\n")
    cwd = tmp_path / "workspace with spaces"
    cwd.mkdir()
    monkeypatch.setenv("A13N_SANDBOX_SKILLS_DIR", str(source))
    monkeypatch.setenv("A13N_ENVD_EXECUTION_UID", str(os.getuid()))
    monkeypatch.setenv("A13N_ENVD_EXECUTION_GID", str(os.getgid()))
    return cwd


def initialize(cwd):
    result = subprocess.run([str(INIT)], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    return result


def test_init_copies_complete_skill_into_startup_cwd(workspace):
    result = initialize(workspace)
    target = workspace / ".agents/skills/agent-browser"
    assert result.stderr == ""
    assert (target / "SKILL.md").read_text().endswith("Guide\n")
    assert (target / "references/commands.md").read_text() == "commands\n"
    assert (target / "SKILL.md").stat().st_uid == os.getuid()
    assert not list(target.parent.glob(".a13n-skill.*"))
    assert not (workspace.parent / ".agents").exists()


def test_init_preserves_existing_skill_without_merging(workspace):
    target = workspace / ".agents/skills/agent-browser"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user version")
    initialize(workspace)
    initialize(workspace)
    assert (target / "SKILL.md").read_text() == "user version"
    assert not (target / "references").exists()


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling-symlink"])
def test_init_preserves_existing_non_directory_targets(workspace, kind):
    target = workspace / ".agents/skills/agent-browser"
    target.parent.mkdir(parents=True)
    outside = workspace / "outside"
    if kind == "file":
        target.write_text("user file")
    else:
        if kind == "symlink":
            outside.mkdir()
        target.symlink_to(outside)
    initialize(workspace)
    if kind == "file":
        assert target.read_text() == "user file"
    else:
        assert target.is_symlink()
        assert not (outside / "SKILL.md").exists()


def test_init_warns_without_blocking_when_destination_is_unusable(workspace):
    (workspace / ".agents").write_text("user file")
    result = initialize(workspace)
    assert "skipping skills" in result.stderr
    assert (workspace / ".agents").read_text() == "user file"


def test_init_does_not_publish_a_partial_copy(workspace, tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    cp = fake_bin / "cp"
    cp.write_text('#!/bin/sh\ntouch "$4/partial"\nexit 1\n')
    cp.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    result = initialize(workspace)
    assert "skipping skill" in result.stderr
    assert list((workspace / ".agents/skills").iterdir()) == []


def test_init_without_bundled_skills_does_not_mutate_cwd(workspace, monkeypatch):
    monkeypatch.setenv("A13N_SANDBOX_SKILLS_DIR", str(workspace / "missing"))
    initialize(workspace)
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize("init_exit", [0, 1])
def test_entrypoint_initializes_then_execs_command_without_stdout_noise(tmp_path, monkeypatch, init_exit):
    init = tmp_path / "a13n-sandbox-init"
    init.write_text(f"#!/bin/sh\ntouch initialized\nexit {init_exit}\n")
    init.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    with subprocess.Popen(
        [str(ENTRYPOINT), "sh", "-c", 'test -f initialized; printf "%s" "$$"; exit 7'],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 7
        assert stdout == str(process.pid)
        assert ("initialization failed" in stderr) == bool(init_exit)
