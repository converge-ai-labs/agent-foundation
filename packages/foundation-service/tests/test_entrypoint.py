import os
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).parents[3] / "scripts" / "docker-entrypoint.sh"


def _run_entrypoint(
    tmp_path: Path,
    *,
    role: str,
    auto_migrate: str,
    serve_args: tuple[str, ...] = (),
) -> list[str]:
    calls = tmp_path / "calls"
    executable = tmp_path / "foundation-service"
    executable.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\n')
    executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FOUNDATION_ROLE": role,
        "FOUNDATION_AUTO_MIGRATE": auto_migrate,
    }

    subprocess.run(
        [str(ENTRYPOINT), "foundation-service", "serve", *serve_args],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    return calls.read_text().splitlines()


def test_execution_role_never_migrates(tmp_path: Path) -> None:
    assert _run_entrypoint(tmp_path, role="execution", auto_migrate="true") == [
        "db current --check-heads",
        "serve",
    ]


def test_all_role_auto_migrates_when_enabled(tmp_path: Path) -> None:
    assert _run_entrypoint(tmp_path, role="all", auto_migrate="true") == ["db upgrade", "serve"]


def test_control_role_checks_heads_when_auto_migrate_is_disabled(tmp_path: Path) -> None:
    assert _run_entrypoint(tmp_path, role="control", auto_migrate="false") == [
        "db current --check-heads",
        "serve",
    ]


def test_cli_role_override_cannot_accidentally_migrate_execution(tmp_path: Path) -> None:
    assert _run_entrypoint(
        tmp_path,
        role="all",
        auto_migrate="true",
        serve_args=("--role", "execution"),
    ) == ["db current --check-heads", "serve --role execution"]


def test_invalid_role_fails_before_running_database_commands(tmp_path: Path) -> None:
    executable = tmp_path / "foundation-service"
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FOUNDATION_ROLE": "invalid",
        "FOUNDATION_AUTO_MIGRATE": "true",
    }

    result = subprocess.run(
        [str(ENTRYPOINT), "foundation-service", "serve"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Invalid FOUNDATION_ROLE" in result.stderr
