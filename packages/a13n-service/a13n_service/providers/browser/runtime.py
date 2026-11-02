"""Standalone Linux Chromium/display runtime shared by Native and the Browser image.

The private profile belongs to one allocation; this process owns one runtime generation.
Operator-installed desktop binaries and the websockets package are required.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import fcntl
import importlib.util
import json
import os
import secrets
import signal
import subprocess
import sys
from functools import partial
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException


def desktop_module(path: Path):
    spec = importlib.util.spec_from_file_location("a13n_browser_desktop_guest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def process_identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except FileNotFoundError:
        return None


def group_alive(group: int) -> bool:
    """Absence is proof; an orphan group blocks reuse rather than guessing its owner."""
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            if fields[0] != "Z" and int(fields[2]) == group:
                return True
        except FileNotFoundError:
            pass
    return False


def parent_exit(parent: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent:
        os._exit(1)


class Runtime:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.root = Path(args.root)
        self.guest = desktop_module(Path(args.guest))
        self.stop = asyncio.Event()
        self.children: list[asyncio.subprocess.Process] = []
        self.nonce = secrets.token_hex(16)
        self.chromium: asyncio.subprocess.Process | None = None
        self.cdp_url: str | None = None

    async def spawn(self, *command: str, **kwargs) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.DEVNULL,
            preexec_fn=partial(self.guest.parent_death_signal, os.getpid()),
            **kwargs,
        )
        self.children.append(process)
        return process

    async def close_chromium(self) -> None:
        """Flush Chromium's profile on runtime or parent exit before signal cleanup."""
        if self.chromium is None or self.chromium.returncode is not None or self.cdp_url is None:
            return
        try:
            async with asyncio.timeout(5):
                async with connect(self.cdp_url, proxy=None, open_timeout=2, close_timeout=1) as connection:
                    await connection.send('{"id":1,"method":"Browser.close"}')
                    # A CDP acknowledgement precedes profile flushing; wait for the owned process.
                    await self.chromium.wait()
        except (OSError, TimeoutError, WebSocketException):
            # A failed browser must still reach the bounded signal cleanup below.
            pass

    async def proxy(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int) -> None:
        async def copy(source, target):
            try:
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
            finally:
                target.close()

        upstream = None
        try:
            remote, upstream = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.gather(copy(reader, upstream), copy(remote, writer))
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            if upstream is not None:
                upstream.close()

    async def serve(self) -> None:
        self.guest.private_directory(self.root)
        for name in ("profile", "desktop", "uploads", "downloads"):
            self.guest.private_directory(self.root / name)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        if self.args.parent:
            parent_exit(self.args.parent)
        with (self.root / "runtime.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            previous = self.root / "runtime.json"
            if previous.exists():
                old = json.loads(previous.read_text())
                if (
                    old["boot"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                    and old["pid"] != os.getpid()
                    and group_alive(old["pid"])
                ):
                    raise RuntimeError("Previous runtime still has live processes")
            info = {
                "runtime_id": self.nonce,
                "pid": os.getpid(),
                "started": process_identity(os.getpid()),
                "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "ready": False,
            }
            self.guest.publish(previous, json.dumps(info))
            try:
                await self.run(info)
            finally:
                await self.close_chromium()
                # End Chromium before the display it uses, even if graceful close failed.
                for process in reversed(self.children):
                    if process.returncode is None:
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                self.guest.publish(previous, json.dumps({**info, "ready": False}))

    async def run(self, info: dict) -> None:
        authority = self.root / "Xauthority"
        cookie = secrets.token_hex(16)
        authority.touch(mode=0o600)
        # The server reads cookie values independently of the display number chosen by -displayfd.
        auth = await self.spawn("xauth", "-f", str(authority), "add", ":0", ".", cookie)
        if await auth.wait() != 0:
            raise RuntimeError("Xauthority setup failed")
        display = await self.spawn(
            "Xvfb",
            "-displayfd",
            "1",
            "-screen",
            "0",
            f"{self.args.width}x{self.args.height}x24",
            "-nolisten",
            "tcp",
            "-auth",
            str(authority),
            stdout=asyncio.subprocess.PIPE,
        )
        assert display.stdout is not None
        number = (await asyncio.wait_for(display.stdout.readline(), 10)).decode().strip()
        if not number.isdecimal():
            raise RuntimeError("Display startup failed")
        name = f":{number}"
        auth = await self.spawn("xauth", "-f", str(authority), "add", name, ".", cookie)
        if await auth.wait() != 0:
            raise RuntimeError("Xauthority setup failed")
        environment = {**os.environ, "DISPLAY": name, "XAUTHORITY": str(authority), "XDG_SESSION_TYPE": "x11"}
        environment.pop("WAYLAND_DISPLAY", None)
        window_manager = await self.spawn("openbox", env=environment)
        # A retained filesystem may contain the old socket after forced container exit.
        (self.root / "desktop" / "control.sock").unlink(missing_ok=True)
        viewer = await self.spawn(
            sys.executable,
            "-I",
            self.args.guest,
            "serve",
            "--root",
            str(self.root / "desktop"),
            "--display",
            name,
            "--authority",
            str(authority),
            "--port",
            str(self.args.viewer_port),
            env=environment,
        )
        endpoint = self.root / "profile" / "DevToolsActivePort"
        endpoint.unlink(missing_ok=True)
        command = [
            self.args.chromium,
            f"--user-data-dir={self.root / 'profile'}",
            "--remote-debugging-port=0",
            "--ozone-platform=x11",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--disable-session-crashed-bubble",
            "--password-store=basic",
            f"--window-size={self.args.width},{self.args.height}",
            "--start-maximized",
            "--restore-last-session",
        ]
        if self.args.no_sandbox:
            command.append("--no-sandbox")
        chromium = self.chromium = await self.spawn(*command, env=environment)
        critical = (display, window_manager, viewer, chromium)
        async with asyncio.timeout(30):
            while not endpoint.exists() or not (self.root / "desktop" / "control.sock").exists():
                if any(child.returncode is not None for child in critical) or self.stop.is_set():
                    raise RuntimeError("Chromium startup failed")
                await asyncio.sleep(0.05)
            port, path = endpoint.read_text().splitlines()[:2]
            self.cdp_url = f"ws://127.0.0.1:{port}{path}"
            description = await self.guest.call(self.root / "desktop", {"protocol_version": 1, "operation": "describe"})
            if "error" in description:
                raise RuntimeError("Viewer startup failed")
        async with await asyncio.start_server(
            partial(self.proxy, port=int(port)), self.args.listen, self.args.cdp_port
        ) as server:
            info.update(
                ready=True,
                cdp_port=server.sockets[0].getsockname()[1],
                cdp_path=path,
                viewer_port=self.args.viewer_port,
                viewer_generation=description["result"]["generation"],
            )
            self.guest.publish(self.root / "runtime.json", json.dumps(info))
            try:
                while not self.stop.is_set():
                    if any(child.returncode is not None for child in critical):
                        raise RuntimeError("A required Browser runtime process exited")
                    try:
                        await asyncio.wait_for(self.stop.wait(), 0.1)
                    except TimeoutError:
                        pass
            finally:
                # wait_closed also waits for accepted CDP connections. End them before
                # leaving this server scope so clients cannot block owned-child cleanup.
                server.close()
                server.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--guest", required=True)
    parser.add_argument("--chromium", default="chromium")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--cdp-port", type=int, default=0)
    parser.add_argument("--viewer-port", type=int, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--parent", type=int)
    parser.add_argument("--gate-fd", type=int)
    parser.add_argument("--no-sandbox", action="store_true")
    args = parser.parse_args()
    if args.gate_fd is not None:
        try:
            if os.read(args.gate_fd, 1) != b"1":
                return
        finally:
            os.close(args.gate_fd)
    asyncio.run(Runtime(args).serve())


if __name__ == "__main__":
    main()
