"""Per-command byte capture and deadline enforcement; never an installed daemon."""

from __future__ import annotations

import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def process_start(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def publish(directory: Path, state: dict) -> None:
    pending = directory / "status.pending"
    pending.write_text(json.dumps(state, separators=(",", ":")))
    pending.replace(directory / "status.json")


def forward_stdin(process: subprocess.Popen, maximum: int, state: dict) -> None:
    assert process.stdin is not None
    written = 0
    try:
        while chunk := os.read(0, 65536):
            if written + len(chunk) > maximum:
                break
            process.stdin.write(chunk)
            process.stdin.flush()
            written += len(chunk)
    except (BrokenPipeError, OSError):
        pass
    finally:
        process.stdin.close()
        state["stdin_open"] = False


def run(plan: dict) -> None:
    directory = Path(plan["directory"])
    with (directory.parent / "allocation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != plan["boot_id"]:
            return
        if sum(1 for item in directory.parent.iterdir() if item.name.startswith("process-")) >= plan["max_processes"]:
            return
        directory.mkdir(mode=0o700)
        (directory / "owner").write_text(plan["owner"])
    (directory / "stdin.remaining").write_text(str(plan["stdin_bytes"]))
    (directory / "stdout").touch(mode=0o600)
    (directory / "stderr").touch(mode=0o600)
    state = {
        "runner_pid": os.getpid(),
        "runner_start": process_start(os.getpid()),
        "started_at": timestamp(),
        "ended_at": None,
        "phase": "starting",
        "exit_code": None,
        "termination_reason": None,
        "signal": None,
        "stdin_open": plan["keep_stdin_open"],
        "stdout_produced": 0,
        "stderr_produced": 0,
        "stdout_stored": 0,
        "stderr_stored": 0,
        "content_complete": True,
        "cleanup": "pending",
    }
    publish(directory, state)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SHELL", "TERM", "TMPDIR"}
    }
    environment.update(plan["environment"]["set"])
    for key in plan["environment"]["unset"]:
        environment.pop(key, None)
    try:
        process = subprocess.Popen(
            plan["argv"],
            cwd=plan["cwd"],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        state.update(
            phase="failed",
            termination_reason="backend_lost",
            ended_at=timestamp(),
            cleanup="complete",
            stdin_open=False,
        )
        publish(directory, state)
        return

    assert process.stdin is not None and process.stdout is not None and process.stderr is not None

    def send_group(number: int) -> None:
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            pass

    def handle_signal(number: int, _frame) -> None:
        translated = signal.SIGKILL if number == signal.SIGUSR1 else number
        state["termination_reason"] = "signal"
        state["signal"] = {signal.SIGINT: "interrupt", signal.SIGTERM: "terminate", signal.SIGKILL: "kill"}[
            signal.Signals(translated)
        ]
        send_group(translated)

    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
        signal.signal(number, handle_signal)
    state["phase"] = "running"
    publish(directory, state)
    if plan["keep_stdin_open"]:
        threading.Thread(target=forward_stdin, args=(process, plan["stdin_bytes"], state), daemon=True).start()
    else:
        process.stdin.close()
    deadline = time.monotonic() + plan["wall_time_seconds"]
    finished_at = None
    try:
        with (
            selectors.DefaultSelector() as selector,
            (directory / "stdout").open("wb", buffering=0) as stdout,
            (directory / "stderr").open("wb", buffering=0) as stderr,
        ):
            selector.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout))
            selector.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr))
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if now >= deadline and process.poll() is None:
                    state["termination_reason"] = "timeout"
                    send_group(signal.SIGKILL)
                if process.poll() is not None:
                    if finished_at is None:
                        finished_at = now
                        send_group(signal.SIGKILL)
                    if now - finished_at > 1:
                        state["content_complete"] = False
                        break
                for key, _ in selector.select(0.1):
                    stream, output = key.data
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    state[f"{stream}_produced"] += len(chunk)
                    room = max(0, plan["max_output_bytes"] - state[f"{stream}_stored"])
                    selected = chunk[:room]
                    output.write(selected)
                    state[f"{stream}_stored"] += len(selected)
                    if len(selected) != len(chunk):
                        state["content_complete"] = False
                        if plan["overflow"] != "truncate":
                            state["termination_reason"] = "output_limit"
                            send_group(signal.SIGKILL)
                publish(directory, state)
    except OSError:
        state["termination_reason"] = "backend_lost"
        state["content_complete"] = False
    finally:
        send_group(signal.SIGKILL)
        code = process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        reason = state["termination_reason"] or ("signal" if code < 0 else "exit")
        phase = {"exit": "exited", "signal": "signaled", "timeout": "timed_out"}.get(reason, "failed")
        state.update(
            phase=phase,
            exit_code=code if code >= 0 else None,
            termination_reason=reason,
            ended_at=timestamp(),
            stdin_open=False,
            cleanup="residual_confined",
        )
        publish(directory, state)


if __name__ == "__main__":
    run(json.loads(sys.argv[1]))
