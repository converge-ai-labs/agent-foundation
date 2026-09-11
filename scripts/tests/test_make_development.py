"""Exercise local Make launchers without starting applications or infrastructure."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
LAUNCHERS = [
    ("cli", "harness-ui", ["a13n-harness-ui", "--no-update-check"]),
    ("harness-ui-smoke", "harness-ui", ["python", "-m", "dev.harness-ui.smoke"]),
    ("harness-dev", "harness", ["opentelemetry-instrument", "python", "dev/observation-demo/agent.py"]),
]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copy2(REPOSITORY_ROOT / "Makefile", tmp_path / "Makefile")
    for profile in ("harness", "harness-ui"):
        directory = tmp_path / "dev" / profile
        directory.mkdir(parents=True)
        (directory / ".env.example").write_text(f"PROFILE={profile}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if '--env-file' in args:\n"
        "    assert Path(args[args.index('--env-file') + 1]).is_file()\n"
        "with Path(os.environ['UV_CALL_LOG']).open('a') as log:\n"
        "    log.write(json.dumps(args) + '\\n')\n"
        "sys.exit(37 if os.environ.get('FAIL_COMMAND') in args else 0)\n"
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("UV_CALL_LOG", str(tmp_path / "uv-calls.jsonl"))
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEFILES", "FAIL_COMMAND", "HARNESS_ENV", "HARNESS_UI_ENV"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def run_make(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def uv_calls(workspace: Path) -> list[list[str]]:
    log = workspace / "uv-calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize(("target", "profile", "command"), LAUNCHERS)
def test_launcher_initializes_environment_before_running(workspace: Path, target, profile, command) -> None:
    result = run_make(workspace, target)
    assert result.returncode == 0, result.stdout + result.stderr
    env_file = workspace / "dev" / profile / ".env"
    assert env_file.read_bytes() == Path(f"{env_file}.example").read_bytes()
    assert "Initialized" in result.stdout
    assert uv_calls(workspace) == [["run", "--locked", "--env-file", f"dev/{profile}/.env", *command]]


def test_env_init_preserves_existing_files_even_with_newer_templates(workspace: Path) -> None:
    files = [workspace / "dev" / profile / ".env" for profile in ("harness", "harness-ui")]
    for env_file in files:
        env_file.write_text("PRIVATE_SETTING=keep-me\n")
        os.utime(env_file, ns=(1, 1))
    result = run_make(workspace, "env-init")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Initialized" not in result.stdout
    for env_file in files:
        assert env_file.read_text() == "PRIVATE_SETTING=keep-me\n"
        assert env_file.stat().st_mtime_ns == 1
    assert uv_calls(workspace) == []


def test_env_init_prepares_both_profiles_without_starting_anything(workspace: Path) -> None:
    result = run_make(workspace, "env-init")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Initialized") == 2
    assert (workspace / "dev/harness/.env").is_file()
    assert (workspace / "dev/harness-ui/.env").is_file()
    assert uv_calls(workspace) == []


@pytest.mark.parametrize(("target", "variable"), [("cli", "HARNESS_UI_ENV"), ("harness-dev", "HARNESS_ENV")])
@pytest.mark.parametrize("existing", [False, True])
def test_custom_path_with_spaces_uses_only_its_own_file_or_template(
    workspace: Path, target, variable, existing
) -> None:
    env_file = workspace / "custom profile.env"
    source = env_file if existing else Path(f"{env_file}.example")
    source.write_text("CUSTOM=yes\n")
    result = run_make(workspace, target, f"{variable}={env_file}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert env_file.read_text() == "CUSTOM=yes\n"
    assert uv_calls(workspace)[0][3] == str(env_file)
    assert not (workspace / "dev/harness/.env").exists()
    assert not (workspace / "dev/harness-ui/.env").exists()


def test_missing_custom_file_and_template_fail_before_launch(workspace: Path) -> None:
    result = run_make(workspace, "cli", "HARNESS_UI_ENV=missing.env")
    assert result.returncode != 0
    assert "Missing environment file: missing.env" in result.stderr
    assert "missing.env.example" in result.stderr
    assert not (workspace / "missing.env").exists()
    assert uv_calls(workspace) == []


def test_parallel_ui_launchers_share_environment_initialization(workspace: Path) -> None:
    result = run_make(workspace, "-j2", "cli", "harness-ui-smoke")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Initialized") == 1
    assert len(uv_calls(workspace)) == 2


@pytest.mark.parametrize("args", [(), ("help",), ("--dry-run", "env-init", "cli", "harness-dev")])
def test_help_and_dry_run_have_no_side_effects(workspace: Path, args) -> None:
    result = run_make(workspace, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (workspace / "dev/harness/.env").exists()
    assert not (workspace / "dev/harness-ui/.env").exists()
    assert uv_calls(workspace) == []
    if not args or args == ("help",):
        assert "Common workflows:" in result.stdout
        assert "CLI_ARGS" in result.stdout
        assert "All targets:" in result.stdout


@pytest.mark.parametrize("target", ["cli", "a13n-harness-ui"])
def test_ui_launchers_forward_arguments(workspace: Path, target) -> None:
    result = run_make(workspace, target, 'CLI_ARGS=--config "config with spaces.yaml" webui')
    assert result.returncode == 0, result.stdout + result.stderr
    assert uv_calls(workspace)[-1][-5:] == [
        "a13n-harness-ui",
        "--no-update-check",
        "--config",
        "config with spaces.yaml",
        "webui",
    ]
    if target == "a13n-harness-ui":
        assert "--env-file" not in uv_calls(workspace)[-1]
        assert not (workspace / "dev/harness-ui/.env").exists()


def test_harness_launcher_forwards_scenario_and_propagates_failure(workspace: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAIL_COMMAND", "summary")
    result = run_make(workspace, "harness-dev", "HARNESS_ARGS=summary")
    assert result.returncode != 0
    assert "Error 37" in result.stderr
    assert uv_calls(workspace)[-1][-1] == "summary"


def test_examples_smoke_stops_when_first_agent_app_run_fails(workspace: Path, monkeypatch) -> None:
    for example in ("agent-app", "environment-provider", "plugins"):
        (workspace / "examples" / example).mkdir(parents=True)
    monkeypatch.setenv("FAIL_COMMAND", "first turn")
    result = run_make(workspace, "examples-smoke")
    assert result.returncode != 0
    assert "Error 37" in result.stderr
    calls = uv_calls(workspace)
    assert "first turn" in calls[-1]
    assert not any("turn after restart" in call for call in calls)
