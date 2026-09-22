"""Native Seatbelt integration: python3 sandbox_macos.py /path/to/a13n-envd.

No root, external network, Homebrew runtime, or browser is required.
"""

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from egress_linux import Device


def context(name):
    return {"operation_id": "op-" + name}


def verify_sandbox(binary, mode):
    with tempfile.TemporaryDirectory(prefix="a13n-seatbelt-") as temporary:
        root = Path(temporary).resolve()
        readonly = root / "readonly"
        readonly.mkdir()
        (readonly / "input").write_text("input")
        private = root / "private"
        private.write_text("not granted")
        device = Device(
            binary,
            root,
            egress=mode,
            execution={},
            sandbox={
                "mode": "restricted",
                "grants": [
                    {"path": str(root / "workspace"), "access": "read_write"},
                    {"path": str(readonly), "access": "read_only"},
                ],
            },
        )
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        try:
            boundary = device.descriptor["boundary"]
            assert boundary["backend"] == "macos_seatbelt", boundary
            assert boundary["egress"] == mode, boundary
            assert not boundary["privilege_gain_blocked"], boundary
            device.open()
            observed = device.call("environment.describe", {"context": context("describe")})["descriptor"]
            assert observed["boundary"] == boundary
            device.success(
                'test -w "$HOME" && test -w "$TMPDIR" && test -z "$HOST_ONLY_SECRET" && test -z "$HTTPS_PROXY" && mkdir visible && printf persisted > written'
            )
            assert (device.workspace / "written").read_text() == "persisted"
            device.call(
                "file.read_text",
                {
                    "context": context("private"),
                    "path": {"path": str(private)},
                    "line_limit": 10,
                    "max_line_length": 1024,
                },
                error="denied",
            )
            result = device.call(
                "file.read_text",
                {
                    "context": context("readonly"),
                    "path": {"path": str(readonly / "input")},
                    "line_limit": 10,
                    "max_line_length": 1024,
                },
            )
            assert result["text"] == "input", result
            device.call(
                "file.write_text",
                {
                    "context": context("denied"),
                    "path": {"path": str(readonly / "output")},
                    "text": "no",
                    "mode": "create",
                },
                error="denied",
            )
            device.call(
                "file.write_text",
                {
                    "context": context("written"),
                    "path": {"path": str(device.workspace / "rpc")},
                    "text": "rpc",
                    "mode": "create",
                },
            )
            device.success(f"test ! -r {shlex.quote(str(private))}; test ! -r {shlex.quote(str(root / 'envd.json'))}")
            connected = device.success(
                f"/bin/bash -c 'if (echo probe > /dev/tcp/127.0.0.1/{port}) 2>/dev/null; then echo yes; else echo no; fi'"
            ).strip()
            assert connected == ("yes" if mode == "inherit" else "no"), connected
            device.close()
            discovery = {
                "expected_device_id": device.descriptor["device_id"],
                "expected_generation": device.descriptor["generation"],
                "path": str(device.workspace),
                "offset": 0,
                "limit": 100,
            }
            assert "visible" in json.dumps(device.call("directory.list", discovery))
            device.call("directory.list", {**discovery, "path": str(root / ".a13n")}, error="denied")
            device.open()
            assert device.success("cat written rpc") == "persistedrpc"
            device.close()
            # The same canonical path cannot silently authorize a replacement root.
            device.workspace.rename(root / "original")
            device.workspace.symlink_to(readonly)
            device.call(
                "session.open",
                {
                    "expected_device_id": device.descriptor["device_id"],
                    "expected_generation": device.descriptor["generation"],
                    "protocol_version": "0.1",
                    "working_directory": str(device.workspace),
                },
                error="unsupported",
            )
        finally:
            listener.close()
            device.shutdown()
    print(f"PASS restricted+{mode}: boundary, files, grants, network, discovery, reopen, replacement")


def verify_cleanup(binary, failure):
    with tempfile.TemporaryDirectory(prefix="a13n-seatbelt-cleanup-") as temporary:
        root = Path(temporary).resolve()
        device = Device(
            binary,
            root,
            egress="deny",
            execution={},
            sandbox={
                "mode": "restricted",
                "grants": [{"path": str(root / "workspace"), "access": "read_write"}],
            },
        )
        try:
            device.open()
            device.call(
                "process.start",
                {
                    "context": context("start"),
                    "request": {
                        "command": {
                            "kind": "shell",
                            "profile_id": device.profile,
                            "script": "while :; do printf x >> heartbeat; sleep 0.05; done",
                        }
                    },
                },
            )
            heartbeat = device.workspace / "heartbeat"
            deadline = time.monotonic() + 10
            while not heartbeat.exists():
                assert time.monotonic() < deadline, "payload did not start"
                time.sleep(0.05)
            if failure == "close":
                device.close()
            elif failure == "broker":
                device.process.kill()
                device.process.wait(timeout=10)
            else:
                rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid=,command="], text=True)
                workers = [
                    int(parts[0])
                    for row in rows.splitlines()
                    if len(parts := row.split(None, 2)) == 3
                    and parts[1] == str(device.process.pid)
                    and "--internal-session-worker" in parts[2]
                ]
                assert len(workers) == 1, workers
                os.kill(workers[0], signal.SIGKILL)
            time.sleep(3)
            size = heartbeat.stat().st_size
            time.sleep(0.3)
            assert heartbeat.stat().st_size == size, f"payload survived {failure}"
        finally:
            device.shutdown()
    print(f"PASS {failure}: managed command stops")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    for mode in ["inherit", "deny"]:
        verify_sandbox(binary, mode)
    for failure in ["close", "worker", "broker"]:
        verify_cleanup(binary, failure)


if __name__ == "__main__":
    main()
