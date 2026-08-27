import subprocess
import sys


def test_module_entrypoint_exposes_surface_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "a13n_ui", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "{webui,tui}" in result.stdout
    assert "default: webui" in result.stdout
