"""Run the packaged POSIX startup owner against real local child processes."""

import os
import select
import signal
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from a13n_service.providers.environment import envd_assets

SUPERVISOR = files("a13n_service.providers.environment").joinpath("envd_supervise.sh")
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Guest startup uses Linux setsid/coreutils")


def line(stream, timeout=5):
    assert select.select([stream], [], [], timeout)[0], "Child did not report startup"
    return stream.readline().strip()


def test_stdio_preserves_peer_pid_input_output_and_exit_status():
    with subprocess.Popen(
        ["sh", str(SUPERVISOR), "sh", "-c", 'read value; printf "%s %s" "$$" "$value"; exit 7'],
        env={**os.environ, "A13N_ENVD_TRANSPORT": "stdio"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        stdout, stderr = process.communicate("hello\n", timeout=5)
        assert (process.returncode, stdout, stderr) == (7, f"{process.pid} hello", "")


@pytest.mark.parametrize("exit_code", [0, 17])
def test_unexpected_exit_has_finite_backoff_and_restart_budget(tmp_path, exit_code):
    result = subprocess.run(
        ["sh", str(SUPERVISOR), "sh", "-c", f"echo started >> attempts; exit {exit_code}"],
        cwd=tmp_path,
        env={**os.environ, "A13N_ENVD_TRANSPORT": "http"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert (tmp_path / "attempts").read_text().splitlines() == ["started"] * 4
    assert "restart 1/3 in 1s" in result.stderr
    assert "restart 2/3 in 2s" in result.stderr
    assert "restart 3/3 in 4s" in result.stderr
    assert f"restart budget exhausted (exit {exit_code})" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("transport", ["http", "reverse_websocket"])
def test_killed_daemon_restarts_and_shutdown_does_not_restart(transport):
    with subprocess.Popen(
        ["sh", str(SUPERVISOR), "sh", "-c", 'sleep 300 & printf "%s %s\\n" "$$" "$!"; wait'],
        env={**os.environ, "A13N_ENVD_TRANSPORT": transport},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            first, descendant = map(int, line(process.stdout).split())
            os.kill(first, signal.SIGKILL)
            second, _ = map(int, line(process.stdout).split())
            assert first != second
            old_child = Path(f"/proc/{descendant}/stat")
            assert not old_child.exists() or old_child.read_text().split()[2] == "Z"
            process.terminate()
            _, stderr = process.communicate(timeout=8)
            assert process.returncode == 0
            assert stderr.count("restart 1/3") == 1
            assert "restart 2/3" not in stderr
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=8)


def test_termination_during_backoff_does_not_start_another_child(tmp_path):
    with subprocess.Popen(
        ["sh", str(SUPERVISOR), "sh", "-c", "echo started >> attempts; exit 3"],
        cwd=tmp_path,
        env={**os.environ, "A13N_ENVD_TRANSPORT": "http"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert "restart 1/3" in line(process.stderr)
            process.terminate()
            process.communicate(timeout=5)
            assert process.returncode == 0
            assert (tmp_path / "attempts").read_text().splitlines() == ["started"]
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)


def test_preparation_manifest_carries_exact_packaged_startup_bytes(tmp_path):
    destination = tmp_path / "supervise"
    command = envd_assets.commands("headless")[-1].replace("/usr/local/bin/a13n-envd-supervise", str(destination))
    subprocess.run(["sh", "-c", command], capture_output=True, check=True, timeout=5)
    assert destination.read_bytes() == SUPERVISOR.read_bytes()
    assert destination.stat().st_mode & 0o777 == 0o755


def test_startup_keeps_preinstalled_legacy_images_usable(tmp_path, monkeypatch):
    entrypoint = tmp_path / "a13n-sandbox-entrypoint"
    entrypoint.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    entrypoint.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(["sh", "-c", envd_assets.startup("a13n-envd")], capture_output=True, text=True, check=True)
    assert result.stdout == "a13n-envd\n"
    supervisor = tmp_path / "a13n-envd-supervise"
    supervisor.write_text("#!/bin/sh\nexit 0\n")
    supervisor.chmod(0o755)
    result = subprocess.run(["sh", "-c", envd_assets.startup("a13n-envd")], capture_output=True, text=True, check=True)
    assert result.stdout == "a13n-envd-supervise\na13n-envd\n"


@pytest.mark.parametrize("installed", [False, True])
def test_desktop_startup_keeps_display_owner_outside_daemon_supervisor(tmp_path, monkeypatch, installed):
    entrypoint = tmp_path / "a13n-sandbox-entrypoint"
    entrypoint.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    entrypoint.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    if installed:
        supervisor = tmp_path / "a13n-envd-supervise"
        supervisor.write_text("#!/bin/sh\nexit 0\n")
        supervisor.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", envd_assets.startup("a13n-sandbox-desktop")], capture_output=True, text=True, check=True
    )
    assert result.stdout.splitlines() == (
        ["a13n-sandbox-desktop", "a13n-envd-supervise", "a13n-envd"] if installed else ["a13n-sandbox-desktop"]
    )


def test_nested_shutdown_reaps_daemon_before_desktop_group_deadline():
    child = None
    with subprocess.Popen(
        ["sh", str(SUPERVISOR), "sh", "-c", 'trap "" TERM; printf "%s\\n" "$$"; sleep 300 & wait'],
        env={**os.environ, "A13N_ENVD_TRANSPORT": "http"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            child = int(line(process.stdout))
            # desktop.py owns this outer group; the daemon owns another session.
            # Reproduce its TERM -> five-second wait -> group KILL sequence.
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            assert process.returncode == 0, "Desktop killed the supervisor before daemon cleanup"
            assert not Path(f"/proc/{child}").exists()
            _, stderr = process.communicate(timeout=3)
            assert "restart" not in stderr
        finally:
            # Even a failing regression test must retire both owned groups.
            for group in (child, process.pid):
                if group is not None:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            process.communicate(timeout=3)


def test_shutdown_bounds_unresponsive_daemon_without_restarting():
    with subprocess.Popen(
        ["sh", str(SUPERVISOR), "sh", "-c", 'trap "" TERM; printf "%s\\n" "$$"; sleep 300 & wait'],
        env={**os.environ, "A13N_ENVD_TRANSPORT": "http"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            child = int(line(process.stdout))
            process.terminate()
            _, stderr = process.communicate(timeout=8)
            assert process.returncode == 0
            assert "restart" not in stderr
            assert not Path(f"/proc/{child}").exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=8)
