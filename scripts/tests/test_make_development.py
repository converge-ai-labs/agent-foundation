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
    ("cli", "harness-ui", ["python", "-m", "dev.harness-ui.cli"]),
    ("harness-ui-smoke", "harness-ui", ["python", "-m", "dev.harness-ui.smoke"]),
    ("harness-dev", "harness", ["opentelemetry-instrument", "python", "dev/observation-demo/agent.py"]),
]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tmp_path = (tmp_path / "workspace with spaces").resolve()
    tmp_path.mkdir()
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
    # The same recorder stands in for asset tooling, not the foreground server.
    shutil.copy2(uv, bin_dir / "pnpm")
    shutil.copy2(uv, bin_dir / "npm")
    shutil.copy2(uv, bin_dir / "python3")
    (tmp_path / "frontend").mkdir()
    for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"):
        (tmp_path / "frontend" / name).touch()
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


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("dev", "dev"),
        ("service-dev", "service-dev"),
        ("setup", "setup"),
        ("dev-down", "down"),
        ("dev-status", "status"),
    ],
)
def test_service_make_targets_use_stdlib_bootstrap_without_implicit_uv_sync(workspace: Path, target, command) -> None:
    result = run_make(workspace, target)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = uv_calls(workspace)
    assert len(calls) == 1
    assert calls[0][:2] == ["-m", "dev.service"]
    assert calls[0][-1] == command
    assert "run" not in calls[0] and "--all-packages" not in calls[0]


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
    assert uv_calls(workspace)[-1][-3:] == ["--config", "config with spaces.yaml", "webui"]
    if target == "a13n-harness-ui":
        assert "--no-update-check" in uv_calls(workspace)[-1]
        assert "--env-file" not in uv_calls(workspace)[-1]
        assert "--data-root" not in uv_calls(workspace)[-1]
        assert not (workspace / "dev/harness-ui/.env").exists()


def test_cli_forwards_explicit_path_overrides_to_development_launcher(workspace: Path) -> None:
    result = run_make(workspace, "cli", 'CLI_ARGS=--config "custom config.yaml" --data-root "custom data" webui')
    assert result.returncode == 0, result.stdout + result.stderr
    assert uv_calls(workspace)[-1][-5:] == [
        "--config",
        "custom config.yaml",
        "--data-root",
        "custom data",
        "webui",
    ]


@pytest.mark.parametrize("options", [[], ["--port", "9000", "--no-share-computer"]])
def test_webui_builds_assets_then_launches_without_authentication_override(workspace: Path, options) -> None:
    result = run_make(
        workspace,
        "webui",
        'CLI_ARGS=--config "custom config.yaml"',
        f"WEBUI_ARGS={' '.join(options)}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = uv_calls(workspace)
    build = next(i for i, call in enumerate(calls) if "build" in call)
    prepare = next(i for i, call in enumerate(calls) if "scripts/prepare-a13n-harness-ui-assets.py" in call)
    assert build < prepare < len(calls) - 1
    assert calls[-1] == [
        "run",
        "--locked",
        "--env-file",
        "dev/harness-ui/.env",
        "python",
        "-m",
        "dev.harness-ui.cli",
        "--config",
        "custom config.yaml",
        "webui",
        *options,
    ]
    assert "--apikey" not in calls[-1] and "--dangerous-skip-permissions" not in calls[-1]


def test_webui_asset_failure_prevents_server_start(workspace: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAIL_COMMAND", "build")
    result = run_make(workspace, "webui")
    assert result.returncode != 0
    assert not any("dev.harness-ui.cli" in call for call in uv_calls(workspace))


@pytest.mark.parametrize("interface", ["cli", "webui"])
def test_landing_skips_development_environment_and_path_overrides(workspace: Path, interface) -> None:
    result = run_make(
        workspace,
        f"{interface}-landing",
        "HARNESS_UI_ENV=missing.env",
        "CLI_ARGS=--config daily.yaml --data-root daily-data",
        "WEBUI_ARGS=--port 9000 --no-share-computer",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = uv_calls(workspace)
    assert calls[-1] == [
        "run",
        "--locked",
        "python",
        "-m",
        "dev.harness-ui.landing",
        interface,
        *(["--port", "9000", "--no-share-computer"] if interface == "webui" else []),
    ]
    assert not (workspace / "dev/harness-ui/.env").exists()
    if interface == "webui":
        build = next(i for i, call in enumerate(calls) if "build" in call)
        prepare = next(i for i, call in enumerate(calls) if "scripts/prepare-a13n-harness-ui-assets.py" in call)
        assert build < prepare < len(calls) - 1
    else:
        assert len(calls) == 1


def test_webui_landing_asset_failure_prevents_launch(workspace: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAIL_COMMAND", "build")
    result = run_make(workspace, "webui-landing")
    assert result.returncode != 0
    assert not any("dev.harness-ui.landing" in call for call in uv_calls(workspace))


def test_cli_configuration_and_data_are_git_ignored() -> None:
    paths = [
        "var/harness-ui/a13n-harness-ui.yaml",
        "var/harness-ui/models/local.yaml",
        "var/harness-ui/data/objects/example.json.zst",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *paths],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == paths


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


@pytest.mark.parametrize("target", ["db-upgrade", "db-check"])
def test_database_targets_require_setup_configuration(workspace: Path, target: str) -> None:
    result = run_make(workspace, target)
    assert result.returncode != 0
    assert "Run make setup first" in result.stderr
    assert not uv_calls(workspace)


@pytest.mark.parametrize("target", ["db-upgrade", "db-check"])
@pytest.mark.parametrize("override", [False, True])
def test_database_targets_use_generated_config_or_explicit_override(
    workspace: Path, target: str, override: bool
) -> None:
    config = "custom config.toml" if override else "var/dev/service.toml"
    path = workspace / config
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[database]\n")
    arguments = [target, f"SERVICE_CONFIG={config}"] if override else [target]
    result = run_make(workspace, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    migrations = [call for call in uv_calls(workspace) if "migrate" in call]
    assert len(migrations) == 1
    call = migrations[0]
    assert call[call.index("--config") + 1] == config
    assert ("--check" in call) == (target == "db-check")
