import json
import re
import subprocess
import sys
from pathlib import Path


def test_module_entrypoint_exposes_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "a13n_ui", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "runtime" in result.stdout
    assert "--config" in result.stdout


def test_module_entrypoint_runs_runtime_status_from_yaml(tmp_path: Path) -> None:
    settings = _write_settings(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a13n_ui",
            "--config",
            str(settings),
            "runtime",
            "status",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["active_generation_id"].startswith("runtime-")
    assert payload["generations"][0]["state"] == "active"


def test_interactive_cli_keeps_host_alive_across_runtime_restart(tmp_path: Path) -> None:
    settings = _write_settings(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "a13n_ui", "--config", str(settings)],
        input="/runtime\n/restart\n/runtime\n/exit\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    selected = re.findall(r"active (runtime-[0-9a-f]+)", result.stdout)
    assert len(selected) == 3
    assert selected[0] != selected[1]
    assert selected[1] == selected[2]


def _write_settings(tmp_path: Path) -> Path:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        f"""
storage:
  data_root: {(tmp_path / "data").as_posix()}
configuration:
  schema_version: "1"
  definition_roots:
    - root_id: root-test
      path: {(tmp_path / "definitions").as_posix()}
      writable: true
runtime:
  startup_timeout_seconds: 3.0
  command_timeout_seconds: 1.0
  drain_timeout_seconds: 1.0
  terminate_timeout_seconds: 1.0
  kill_timeout_seconds: 1.0
""".strip()
        + "\n"
    )
    return settings
