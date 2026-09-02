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
    assert "runtime" not in result.stdout
    assert "--config" in result.stdout


def test_terminal_frontend_uses_process_local_app(tmp_path: Path) -> None:
    settings = _write_settings(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "a13n_ui", "--config", str(settings), "cli"],
        input="/status\n/exit\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "ready pid=process-" in result.stdout
    assert "runtime-" not in result.stdout


def _write_settings(tmp_path: Path) -> Path:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        f"""
schema_version: "1"
process:
  storage:
    data_root: {(tmp_path / "data").as_posix()}
""".strip()
        + "\n"
    )
    return settings
