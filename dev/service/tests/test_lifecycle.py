"""Foreground lifecycle owns all process groups under failures and signals."""

import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dev.service.lifecycle import (
    ProcessSpec,
    background_applications,
    lifecycle_lock,
    stop_background_applications,
    supervise,
)

FIXTURE = Path(__file__).with_name("process_fixture.py")


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


def test_failed_second_spawn_cleans_first_real_process_group(tmp_path, capsys):
    with pytest.raises(FileNotFoundError):
        supervise(
            tmp_path,
            (
                ProcessSpec("first", (sys.executable, str(FIXTURE), "sleep")),
                ProcessSpec("missing", (str(tmp_path / "missing-executable"),)),
            ),
        )
    pid = int(re.search(r"Started first \(pid (\d+)\)", capsys.readouterr().out).group(1))
    assert _gone(pid)


def test_sigterm_cleans_both_real_process_groups(tmp_path):
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE), "supervisor"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    pids = [int(re.search(r"pid (\d+)", process.stdout.readline()).group(1)) for _ in range(2)]
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=10) == 128 + signal.SIGTERM
    assert all(_gone(pid) for pid in pids)


def test_exited_group_leader_does_not_leak_descendant(tmp_path):
    descendant = tmp_path / "descendant"
    with pytest.raises(RuntimeError, match="tree exited"):
        supervise(tmp_path, (ProcessSpec("tree", (sys.executable, str(FIXTURE), "tree", str(descendant))),))
    deadline = time.monotonic() + 5
    while not descendant.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert descendant.exists()
    assert _gone(int(descendant.read_text()))


def test_lifecycle_lock_excludes_start_reset_and_down_processes(tmp_path):
    with lifecycle_lock(tmp_path):
        with pytest.raises(ValueError, match="already starting, running, resetting, or stopping"):
            with lifecycle_lock(tmp_path):
                pass


def test_stop_background_applications_signals_recorded_lock_owner(tmp_path):
    marker = tmp_path / "ready"
    process = subprocess.Popen([sys.executable, str(FIXTURE), "background", str(tmp_path), str(marker)])
    try:
        deadline = time.monotonic() + 5
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert stop_background_applications(tmp_path)
        assert process.wait(timeout=5) == -signal.SIGTERM
        assert not stop_background_applications(tmp_path)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_stop_background_applications_ignores_unlocked_stale_pid(tmp_path):
    state = tmp_path / "var/dev/applications.pid"
    state.parent.mkdir(parents=True)
    state.write_text(f"{os.getpid()}\n")

    assert not stop_background_applications(tmp_path)


@pytest.mark.parametrize("arguments", [("service-dev",), ("reset", "empty"), ("down",)])
def test_real_cli_rejects_concurrent_start_reset_and_down(tmp_path, arguments):
    with lifecycle_lock(tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "dev.service", "--instance-root", str(tmp_path), *arguments],
            cwd=FIXTURE.parents[3],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    assert result.returncode == 1
    assert "already starting, running, resetting, or stopping" in result.stderr


def test_second_signal_during_drain_forces_only_owned_group(tmp_path):
    marker = tmp_path / "drain"
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE), "stubborn-supervisor", str(marker)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    child = int(re.search(r"pid (\d+)", process.stdout.readline()).group(1))
    try:
        for expected in ("ready", "draining"):
            deadline = time.monotonic() + 5
            while not marker.exists() or marker.read_text() != expected:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 128 + signal.SIGTERM
        assert _gone(child)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if not _gone(child):
            os.killpg(child, signal.SIGKILL)


def test_duplicate_background_owner_preserves_running_state(tmp_path):
    state = tmp_path / "var/dev/applications.pid"
    with background_applications(tmp_path):
        expected = state.read_text()
        with pytest.raises(ValueError, match="already running"):
            with background_applications(tmp_path):
                pass
        assert state.read_text() == expected
    assert state.read_text() == ""


def test_detached_applications_survive_launcher_exit_and_stop_together(tmp_path):
    sockets = [socket.socket() for _ in range(3)]
    try:
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        ports = [listener.getsockname()[1] for listener in sockets]
    finally:
        for listener in sockets:
            listener.close()
    try:
        # communicate() also proves the daemon has released the launcher's pipes.
        result = subprocess.run(
            [sys.executable, str(FIXTURE), "detached", str(tmp_path), *map(str, ports)],
            capture_output=True,
            text=True,
            timeout=40,
            check=True,
        )
        assert "running in the background" in result.stdout
        pids = [int((tmp_path / str(port)).read_text()) for port in ports]
        assert all(not _gone(pid) for pid in pids)
        for port in ports:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
        with pytest.raises(ValueError, match="already starting, running"):
            with lifecycle_lock(tmp_path):
                pass
        assert stop_background_applications(tmp_path)
        assert all(_gone(pid) for pid in pids)
        with lifecycle_lock(tmp_path):
            pass
        assert not stop_background_applications(tmp_path)
    finally:
        stop_background_applications(tmp_path)


def test_detached_startup_failure_is_reported_and_releases_locks(tmp_path):
    result = subprocess.run(
        [sys.executable, str(FIXTURE), "detached-failure", str(tmp_path), "0", "0", "0"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "exited during startup" in result.stderr
    assert "Injected application startup failure" in (tmp_path / "var/dev/applications.log").read_text()
    assert not stop_background_applications(tmp_path)
    with lifecycle_lock(tmp_path):
        pass
