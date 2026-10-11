"""Socket ownership and bounded lifecycle for local HTTP fixtures."""

import argparse
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx2
import uvicorn
from fastapi import FastAPI


def add_listener_arguments(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    """Select POSIX descriptor inheritance or Windows socket sharing over stdin."""
    listener = parser.add_mutually_exclusive_group(required=required)
    listener.add_argument("--fd", type=int)
    listener.add_argument("--shared-socket", action="store_true")


def serve_fixture(app: FastAPI, *, fd: int | None, shared_socket: bool, port: int = 0) -> None:
    """Serve the listener held by the parent, without closing and rebinding its port."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    if shared_socket:
        listener = socket.fromshare(sys.stdin.buffer.read())
    elif fd is not None:
        listener = socket.socket(fileno=fd)
    else:
        server.run()
        return
    with listener:
        server.run(sockets=[listener])


@contextmanager
def fixture_process(module: str, *, port: int = 0, arguments: tuple[str, ...] = ()):
    """Own a real fixture subprocess and its bound listener through cleanup."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen()
        port = listener.getsockname()[1]
        shared_socket = os.name == "nt"
        listener_arguments = ["--shared-socket"] if shared_socket else ["--fd", str(listener.fileno())]
        process = subprocess.Popen(
            [sys.executable, "-m", module, *listener_arguments, *arguments],
            pass_fds=() if shared_socket else (listener.fileno(),),
            stdin=subprocess.PIPE if shared_socket else subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.DEVNULL,
        )
        try:
            if shared_socket:
                assert process.stdin is not None
                with process.stdin:
                    process.stdin.write(listener.share(process.pid))
            with httpx2.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Local fixture process exited during startup")
                    try:
                        response = client.get(f"http://127.0.0.1:{port}/healthz")
                        response.raise_for_status()
                        break
                    except httpx2.HTTPError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Local fixture did not become ready") from None
                        time.sleep(0.1)
            yield f"http://127.0.0.1:{port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
