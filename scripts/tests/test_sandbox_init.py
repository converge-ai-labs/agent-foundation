from __future__ import annotations

import json
import os
import shutil
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


@pytest.mark.parametrize("existing", [False, True])
def test_http_bootstrap_creates_or_reuses_protected_credential(tmp_path, monkeypatch, existing):
    init = tmp_path / "a13n-sandbox-init"
    init.write_text("#!/bin/sh\nexit 0\n")
    init.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("A13N_ENVD_TRANSPORT", "http")
    credential = tmp_path / "credential" / "token"
    monkeypatch.setenv("A13N_ENVD_HTTP_CREDENTIAL_FILE", str(credential))
    if existing:
        credential.parent.mkdir()
        credential.write_text("injected-test-credential")
        credential.chmod(0o400)
        credential.parent.chmod(0o500)
    original = None
    try:
        for _ in range(2):
            result = subprocess.run([str(ENTRYPOINT), "true"], capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            assert result.stdout == ""
            value = credential.read_text()
            assert value == "injected-test-credential" if existing else len(value) >= 32
            assert credential.stat().st_mode & 0o777 == (0o400 if existing else 0o600)
            if original is None:
                original = value
            assert value == original
    finally:
        if credential.parent.exists():
            credential.parent.chmod(0o700)


@pytest.mark.parametrize("transport", ["http", "reverse_websocket"])
def test_headless_credentials_need_no_python_or_skill_initializer(tmp_path, transport):
    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("sh", "mkdir", "dirname", "mktemp", "od", "tr", "ln", "rm", "mv", "cat", "test"):
        (commands / name).symlink_to(shutil.which(name))
    credential = tmp_path / "private directory" / "token"
    env = {
        "PATH": str(commands),
        "A13N_ENVD_TRANSPORT": transport,
        "A13N_ENVD_HTTP_CREDENTIAL_FILE": str(credential),
        "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential),
    }
    if transport == "reverse_websocket":
        env["A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN"] = "test-bootstrap-credential"
    result = subprocess.run(
        [
            str(ENTRYPOINT),
            "sh",
            "-c",
            'test -z "${A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN:-}" && cat "$1"',
            "sh",
            str(credential),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert credential.stat().st_mode & 0o777 == 0o600
    assert not list(credential.parent.glob(".*token*"))
    if transport == "reverse_websocket":
        assert result.stdout == "test-bootstrap-credential"
    else:
        assert len(result.stdout) == 64
        int(result.stdout, 16)


@pytest.mark.parametrize("transport", ["stdio", "http", "reverse_websocket"])
@pytest.mark.parametrize(
    "arguments",
    [
        ["a13n-envd"],
        ["a13n-envd", "--device-id", "device-image"],
        ["a13n-envd", "--help"],
        ["a13n-envd", "--version"],
        ["a13n-envd", "daemon"],
        ["a13n-envd", "supervise"],
        ["a13n-envd", "connect", "https://host.example"],
        ["a13n-sandbox-desktop"],
        ["a13n-sandbox-desktop", "a13n-envd"],
        ["a13n-sandbox-desktop", "a13n-envd", "daemon"],
        ["a13n-sandbox-desktop", "a13n-envd", "supervise"],
        ["a13n-sandbox-desktop", "sh", "-c", "custom command"],
    ],
)
def test_entrypoint_supervises_default_network_daemon_and_preserves_explicit_commands(
    tmp_path, monkeypatch, arguments, transport
):
    expected = list(arguments)
    desktop = expected[0] == "a13n-sandbox-desktop"
    if desktop:
        expected[0] = "desktop"
        if len(expected) == 1:
            expected.append("a13n-envd")
    index = 1 if desktop else 0
    daemon = expected[index:]
    if daemon[0] == "a13n-envd":
        explicit = len(daemon) > 1 and daemon[1] in {"daemon", "supervise", "connect", "--help", "--version"}
        if transport != "stdio" and not explicit:
            expected.insert(index + 1, "supervise")
        if not desktop:
            expected[0] = "envd"
    assert_entrypoint_arguments(tmp_path, monkeypatch, arguments, expected, transport=transport)


def assert_entrypoint_arguments(tmp_path, monkeypatch, arguments, expected, *, transport="http"):
    for executable, label in (
        ("a13n-sandbox-desktop", "desktop"),
        ("a13n-envd", "envd"),
    ):
        script = tmp_path / executable
        script.write_text(f'#!/bin/sh\nprintf "%s\\n" "{label}" "$@"\n')
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("A13N_ENVD_TRANSPORT", transport)
    monkeypatch.setenv("A13N_ENVD_HTTP_CREDENTIAL_FILE", str(tmp_path / "credential"))
    result = subprocess.run([str(ENTRYPOINT), *arguments], capture_output=True, text=True, check=True, timeout=5)
    assert result.stdout.splitlines() == expected


def test_desktop_image_default_command_uses_supervised_daemon(tmp_path, monkeypatch):
    dockerfile = (ENTRYPOINT.parent / "Dockerfile").read_text()
    entrypoints = [
        json.loads(line.removeprefix("ENTRYPOINT "))
        for line in dockerfile.splitlines()
        if line.startswith("ENTRYPOINT ")
    ]
    command = [json.loads(line.removeprefix("CMD ")) for line in dockerfile.splitlines() if line.startswith("CMD ")][-1]
    desktop_entrypoint = next(entrypoint for entrypoint in entrypoints if "a13n-sandbox-desktop" in entrypoint)
    arguments = desktop_entrypoint[desktop_entrypoint.index("a13n-sandbox-entrypoint") + 1 :] + command
    assert_entrypoint_arguments(tmp_path, monkeypatch, arguments, ["desktop", "a13n-envd", "supervise"])
    assert "envd_supervise.sh" not in dockerfile
