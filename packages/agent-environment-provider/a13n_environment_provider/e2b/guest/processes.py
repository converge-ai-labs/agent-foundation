"""Bounded inspection and control of this Provider's command records."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import shutil
import signal
import socket
import sys
from pathlib import Path


def execute(request: dict) -> dict:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    root = Path(request["root"])
    action = request["action"]
    if action == "prepare":
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (root / "allocation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            marker = root / "boot-id"
            if not marker.exists() or marker.read_text() != boot:
                for directory in root.glob("process-*"):
                    shutil.rmtree(directory)
                marker.write_text(boot)
        return {"boot_id": boot}
    if boot != request["boot_id"]:
        return {"error": "environment_stale_mount"}
    if action == "port":
        with socket.socket() as connection:
            connection.settimeout(0.5)
            return {"listening": connection.connect_ex(("127.0.0.1", request["port"])) == 0}
    if action == "owned":
        return {
            "process_ids": [
                directory.name
                for directory in root.glob("process-*")
                if (directory / "owner").read_text() == request["owner"]
            ]
        }
    directory = root / request["process_id"]
    if directory.parent != root:
        raise ValueError("invalid process")
    state = json.loads((directory / "status.json").read_text())
    if action in {"inspect", "rebind"}:
        if action == "rebind" and (directory / "process.released").exists():
            raise FileNotFoundError()
        if state["phase"] in {"running", "starting"}:
            try:
                actual = Path(f"/proc/{state['runner_pid']}/stat").read_text().rsplit(")", 1)[1].split()[19]
            except FileNotFoundError:
                actual = None
            if actual != state["runner_start"]:
                state.update(
                    phase="failed", termination_reason="backend_lost", cleanup="failed", content_complete=False
                )
        return state
    if action == "signal":
        if sys.platform != "linux":
            return {"error": "environment_unsupported"}
        if state["phase"] not in {"running", "starting"}:
            return {}
        actual = Path(f"/proc/{state['runner_pid']}/stat").read_text().rsplit(")", 1)[1].split()[19]
        if actual != state["runner_start"]:
            return {"error": "environment_stale_mount"}
        number = {"interrupt": signal.SIGINT, "terminate": signal.SIGTERM, "kill": signal.SIGUSR1}[request["signal"]]
        # Hold the kernel process identity across inspection and signaling.
        descriptor = os.pidfd_open(state["runner_pid"])
        try:
            actual = Path(f"/proc/{state['runner_pid']}/stat").read_text().rsplit(")", 1)[1].split()[19]
            if actual != state["runner_start"]:
                return {"error": "environment_stale_mount"}
            signal.pidfd_send_signal(descriptor, number)
        finally:
            os.close(descriptor)
        return {}
    if action == "reserve_stdin":
        if not state["stdin_open"] or state["phase"] not in {"running", "starting"}:
            return {"error": "environment_conflict"}
        counter = directory / "stdin.remaining"
        remaining = int(counter.read_text())
        if request["bytes"] < 0 or request["bytes"] > remaining:
            return {"error": "environment_too_large"}
        counter.write_text(str(remaining - request["bytes"]))
        return {}
    if action == "read":
        stream = request["stream"]
        if stream not in {"stdout", "stderr"}:
            raise ValueError("invalid stream")
        with (directory / stream).open("rb") as source:
            source.seek(request["offset"])
            data = source.read(request["length"])
        return {"data": base64.b64encode(data).decode()}
    if action in {"release_output", "release_process"}:
        if state["phase"] in {"running", "starting"}:
            return {"error": "environment_conflict"}
        if action == "release_process":
            (directory / "process.released").touch()
        else:
            stream = request["stream"]
            if stream not in {"stdout", "stderr"}:
                raise ValueError("invalid stream")
            (directory / stream).unlink(missing_ok=True)
        if (directory / "process.released").exists() and not any(
            (directory / stream).exists() for stream in ("stdout", "stderr")
        ):
            shutil.rmtree(directory)
        return {}
    if action == "release":
        if state["phase"] in {"running", "starting"}:
            return {"error": "environment_conflict"}
        shutil.rmtree(directory)
        return {}
    raise ValueError("invalid action")


def main() -> None:
    signal.alarm(30)
    try:
        request = json.loads(sys.argv[1])
        if request["action"] in {"prepare", "port"}:
            result = execute(request)
        else:
            with (Path(request["root"]) / "allocation.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = execute(request)
    except FileNotFoundError:
        result = {"error": "environment_not_found"}
    except PermissionError:
        result = {"error": "environment_denied"}
    except (ValueError, KeyError):
        result = {"error": "environment_request_invalid"}
    except OSError:
        result = {"error": "environment_provider_failure"}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
