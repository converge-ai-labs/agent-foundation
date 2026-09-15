"""Foreground lifecycle owns all process groups under failures and signals."""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dev.service.lifecycle import ProcessSpec, lifecycle_lock, supervise

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
