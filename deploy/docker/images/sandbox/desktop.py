#!/usr/bin/env python3
"""Start one disposable X11 desktop and supervise it with the requested daemon."""

from __future__ import annotations

import contextlib
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path


class Stopped(Exception):
    pass


def main() -> int:
    if os.geteuid() != 0:
        raise RuntimeError("The desktop launcher requires root; Session commands run as sandbox")
    uid = int(os.environ.get("A13N_ENVD_EXECUTION_UID", "1000"))
    gid = int(os.environ.get("A13N_ENVD_EXECUTION_GID", "1000"))
    Path("/tmp/.X11-unix").mkdir(mode=0o1777, exist_ok=True)
    os.chmod("/tmp/.X11-unix", 0o1777)
    directory = Path("/run/a13n-desktop")
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, uid, gid)
    authority = directory / "Xauthority"
    authority.touch(mode=0o600)
    os.chown(authority, uid, gid)
    environment = {
        **os.environ,
        "DISPLAY": ":99",
        "XAUTHORITY": str(authority),
        "HOME": "/home/sandbox",
        "A13N_ENVD_COMPUTER_USE": "true",
    }
    subprocess.run(
        ["xauth", "-f", str(authority), "add", ":99", ".", secrets.token_hex(16)],
        env=environment,
        user=uid,
        group=gid,
        check=True,
        timeout=5,
        stdout=sys.stderr,
    )
    children: list[subprocess.Popen] = []

    def interrupted(_signum, _frame):
        raise Stopped

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)

    def start(command: list[str], *, desktop: bool) -> subprocess.Popen:
        process = subprocess.Popen(
            command,
            env=environment,
            user=uid if desktop else None,
            group=gid if desktop else None,
            stdin=subprocess.DEVNULL if desktop else None,
            stdout=sys.stderr if desktop else None,
            start_new_session=True,
        )
        children.append(process)
        return process

    def wait_ready(command: list[str], *, marker: bytes | None = None) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("Desktop process exited during startup")
            result = subprocess.run(command, env=environment, capture_output=True, timeout=2, check=False)
            if result.returncode == 0 and (marker is None or marker in result.stdout):
                return
            time.sleep(0.1)
        raise RuntimeError("Desktop readiness timed out")

    try:
        # Keep the display stable between the readiness probe and window-manager
        # attachment; X11 otherwise resets when its last temporary client leaves.
        start(
            ["Xvfb", ":99", "-noreset", "-screen", "0", "1280x800x24", "-nolisten", "tcp", "-auth", str(authority)],
            desktop=True,
        )
        wait_ready(["xdpyinfo"])
        start(["openbox"], desktop=True)
        wait_ready(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], marker=b"window id")
        daemon = start(sys.argv[1:] or ["a13n-envd"], desktop=False)
        while all(child.poll() is None for child in children):
            time.sleep(0.1)
        if daemon.returncode is not None:
            return daemon.returncode if daemon.returncode >= 0 else 128 - daemon.returncode
        raise RuntimeError("A required desktop process exited")
    except Stopped:
        return 0
    finally:
        # A second termination signal must not interrupt cleanup of owned process groups.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        for child in reversed(children):
            if child.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        for child in reversed(children):
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Desktop startup failed: {error}", file=sys.stderr)
        sys.exit(1)
