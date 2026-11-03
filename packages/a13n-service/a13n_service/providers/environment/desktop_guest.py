"""Standalone Linux desktop access supervisor, invoked through trusted provider management.

The guest needs Python 3.10+, x11vnc and websockify. No dependency is installed at runtime.
Unix-socket administration is private to the guest account; it is not an Agent tool.
Guest administrators remain trusted. Service owns human identity and Agent admission.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import math
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal

MAX_MESSAGE = 16384
MAX_GRANTS = 16
MAX_HISTORY = 4096
MAX_TTL = 60


class Refused(Exception):
    """A bounded, non-secret protocol error code."""


def parent_death_signal(parent: int) -> None:
    """Linux child processes must not retain access after their supervisor is killed."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        os._exit(1)


def private_directory(root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = root.lstat()
    if root.is_symlink() or not root.is_dir() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise Refused("unsafe_runtime_directory")


def publish(path: Path, contents: str) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def control_address(root: Path):
    """Keep the filesystem socket private without Linux's sockaddr_un path-length limit."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield f"/proc/self/fd/{descriptor}/control.sock"
    finally:
        os.close(descriptor)


@dataclass
class Grant:
    id: str
    mode: Literal["view", "control"]
    epoch: int
    token: str = field(repr=False)
    password: str = field(repr=False)
    process: asyncio.subprocess.Process = field(repr=False)
    port: int
    deadline: float
    expires_at: float
    expiry_timer: asyncio.TimerHandle | None = field(default=None, repr=False)
    state: str = "active"

    def remaining(self) -> float:
        # VM suspend/restore can stop the monotonic clock. Neither clock may extend a grant.
        return min(self.deadline - time.monotonic(), self.expires_at - time.time())

    def public(self, generation: str, *, credential: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "generation": generation,
            "mode": self.mode,
            "epoch": self.epoch,
            "state": self.state,
            "remaining_seconds": max(0, self.remaining()) if self.state == "active" else 0,
        }
        if credential and self.state == "active":
            result.update(token=self.token, password=self.password)
        return result


class Supervisor:
    def __init__(self, root: Path, display: str, authority: str | None, port: int, proxy: str):
        self.root, self.display, self.authority = root, display, authority
        self.port, self.proxy = port, proxy
        self.generation = secrets.token_hex(16)
        self.epoch = 0
        self.grants: dict[str, Grant] = {}
        self.revocations: dict[str, int] = {}
        self.browser_episode = 0
        self.browser_id: str | None = None
        self.browser_closed = False
        self.browser_grants: dict[str, int] = {}
        self.lock = asyncio.Lock()
        self.proxy_process: asyncio.subprocess.Process | None = None
        self.stop = asyncio.Event()

    async def spawn(self, *command: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=partial(parent_death_signal, os.getpid()),
        )

    async def terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:  # Python 3.10 guest compatibility.  # noqa: UP041
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(process.wait(), 2)

    def tokens(self) -> None:
        publish(
            self.root / "tokens",
            "".join(f"{g.token}: 127.0.0.1:{g.port}\n" for g in self.grants.values() if g.state == "active"),
        )

    def schedule_expiry(self, grant: Grant) -> None:
        if grant.expiry_timer is not None:
            grant.expiry_timer.cancel()
        grant.expiry_timer = asyncio.get_running_loop().call_later(max(0, grant.remaining()), self.expire_input, grant)

    def expire_input(self, grant: Grant) -> None:
        # Never wait for the management lock or another grant's X-server cleanup
        # to remove expired input authority. Reconciliation releases held input.
        if grant.state != "active":
            return
        if grant.remaining() > 0:
            self.schedule_expiry(grant)
        elif grant.process.returncode is None:
            try:
                grant.process.kill()
            except ProcessLookupError:
                pass

    async def revoke(self, grant: Grant, state: str = "revoked") -> None:
        if grant.state in {"revoked", "expired", "failed"}:
            return
        if grant.state == "active":
            # Prevent reconnect first, then close every established connection to this grant's backend.
            grant.state = "revoking"
            self.tokens()
        await self.terminate(grant.process)
        if grant.expiry_timer is not None:
            grant.expiry_timer.cancel()
        if grant.mode == "control":
            await self.settle()
        grant.state = state
        (self.root / (grant.id + ".password")).unlink(missing_ok=True)

    async def reap(self) -> None:
        blocked = False
        for grant in self.grants.values():
            try:
                if grant.state == "revoking":
                    await self.revoke(grant)
                if grant.state == "active" and (grant.remaining() <= 0 or grant.process.returncode is not None):
                    await self.revoke(grant, "expired" if grant.remaining() <= 0 else "failed")
            except Refused:
                blocked = True
        if blocked:
            raise Refused("input_cleanup_unconfirmed")
        for identity in list(self.browser_grants):
            grant = self.grants[identity]
            if grant.state in {"revoked", "expired", "failed"}:
                if identity == self.browser_id:
                    self.browser_closed = True
                del self.grants[identity]
                del self.browser_grants[identity]

    def deadline(self, request: dict[str, Any]) -> tuple[float, float]:
        desired = request.get("expires_at")
        if (
            isinstance(desired, bool)
            or not isinstance(desired, (int, float))
            or not math.isfinite(desired)
            or desired <= 0
        ):
            raise Refused("invalid_expiry")
        current = time.time()
        remaining = min(MAX_TTL, desired - current)
        if remaining <= 0:
            raise Refused("grant_expired")
        return time.monotonic() + remaining, current + remaining

    def extend(self, grant: Grant, request: dict[str, Any]) -> None:
        deadline, expires_at = self.deadline(request)
        if grant.state != "active" or grant.remaining() <= 0:
            raise Refused("grant_not_active")
        # A delayed shorter renewal cannot truncate current authority. The wall
        # deadline also prevents retries from renewing beyond the accepted intent.
        grant.deadline = max(grant.deadline, deadline)
        grant.expires_at = max(grant.expires_at, expires_at)
        self.schedule_expiry(grant)

    def browser_fence(self, request: dict[str, Any], *, revoke: bool) -> tuple[str, int]:
        identity, episode = request.get("id"), request.get("episode")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z][a-z0-9_]{7,71}", identity):
            raise Refused("invalid_grant_id")
        if type(episode) is not int or episode < 1:
            raise Refused("invalid_episode")
        if episode > self.browser_episode:
            self.browser_episode, self.browser_id, self.browser_closed = episode, identity, revoke
        elif episode == self.browser_episode:
            if identity != self.browser_id:
                raise Refused("grant_conflict")
            if revoke:
                self.browser_closed = True
        elif not revoke:
            raise Refused("grant_not_active")
        if not revoke and self.browser_closed:
            raise Refused("grant_not_active")
        return identity, episode

    async def ensure(self, request: dict[str, Any]) -> dict[str, Any]:
        """Ensure one Browser control grant for the accepted episode and deadline."""
        identity, expires_at = request.get("id"), request.get("expires_at")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z][a-z0-9_]{7,71}", identity):
            raise Refused("invalid_grant_id")
        if (
            not isinstance(expires_at, (int, float))
            or isinstance(expires_at, bool)
            or not math.isfinite(expires_at)
            or expires_at <= time.time()
        ):
            raise Refused("invalid_expiry")
        identity, episode = self.browser_fence(request, revoke=False)
        existing = self.grants.get(identity)
        if existing is not None:
            if self.browser_grants.get(identity) != episode:
                raise Refused("grant_conflict")
            if existing.state != "active" or existing.remaining() <= 0:
                raise Refused("grant_not_active")
            if expires_at > existing.expires_at:
                existing.deadline = time.monotonic() + (expires_at - time.time())
                existing.expires_at = expires_at
                self.schedule_expiry(existing)
        else:
            try:
                await self.grant(
                    {"id": identity, "mode": "control", "epoch": self.epoch + 1},
                    absolute_expiry=expires_at,
                )
            finally:
                # A failed publication can leave a process record requiring cleanup.
                if identity in self.grants:
                    self.browser_grants[identity] = episode
            existing = self.grants[identity]
        if existing.remaining() <= 0:
            await self.revoke(existing, "expired")
            raise Refused("grant_not_active")
        return {**existing.public(self.generation, credential=True), "expires_at": existing.expires_at}

    async def grant(self, request: dict[str, Any], *, absolute_expiry: float | None = None) -> dict[str, Any]:
        identity, mode, epoch = request.get("id"), request.get("mode"), request.get("epoch")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z][a-z0-9_]{7,71}", identity):
            raise Refused("invalid_grant_id")
        if mode not in ("view", "control") or type(epoch) is not int or epoch < 1:
            raise Refused("invalid_grant")
        if absolute_expiry is None:
            self.deadline(request)
        if identity in self.revocations:
            raise Refused("grant_not_active")
        if existing := self.grants.get(identity):
            if (existing.mode, existing.epoch) != (mode, epoch):
                raise Refused("grant_conflict")
            if existing.state == "active":
                self.extend(existing, request)
            return existing.public(self.generation, credential=True)
        if (
            len(self.grants) + len(self.revocations) >= MAX_HISTORY
            or sum(g.state == "active" for g in self.grants.values()) >= MAX_GRANTS
        ):
            raise Refused("capacity_exceeded")
        if mode == "control":
            if epoch <= self.epoch:
                raise Refused("stale_epoch")
            if any(g.mode == "control" and g.state in ("active", "revoking") for g in self.grants.values()):
                raise Refused("controller_exists")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        password = "".join(
            secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(8)
        )
        password_file = self.root / (identity + ".password")
        writer = await self.spawn("x11vnc", "-storepasswd", password, str(password_file))
        try:
            if await asyncio.wait_for(writer.wait(), 5) != 0:
                raise Refused("password_setup_failed")
        finally:
            await self.terminate(writer)
        command = [
            "x11vnc",
            "-display",
            self.display,
            "-rfbauth",
            str(password_file),
            "-listen",
            "127.0.0.1",
            "-rfbport",
            str(port),
            "-no6",
            "-noshm",
            "-forever",
            "-shared",
            "-noremote",
            "-nosel",
            "-noclipboard",
            "-nosetclipboard",
            "-nocmds",
            "-norc",
        ]
        if self.authority:
            command += ["-auth", self.authority]
        if mode == "view":
            command.append("-viewonly")
        process = await self.spawn(*command)

        try:
            await self.wait_ready(process, port)
            if absolute_expiry is None:
                deadline, expires_at = self.deadline(request)
            else:
                expires_at = absolute_expiry
                deadline = time.monotonic() + (expires_at - time.time())
                if expires_at <= time.time():
                    raise Refused("grant_expired")
            grant = Grant(
                identity,
                mode,
                epoch,
                secrets.token_urlsafe(32),
                password,
                process,
                port,
                deadline,
                expires_at,
            )
            self.grants[identity] = grant
            if mode == "control":
                self.epoch = epoch
            self.tokens()
            self.schedule_expiry(grant)
            return grant.public(self.generation, credential=True)
        except BaseException:
            await self.terminate(process)
            password_file.unlink(missing_ok=True)
            raise

    async def wait_ready(self, process: asyncio.subprocess.Process, port: int) -> None:
        async def listening() -> None:
            while True:
                if process.returncode is not None:
                    raise Refused("desktop_endpoint_failed")
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    return
                except OSError:
                    await asyncio.sleep(0.05)

        await asyncio.wait_for(listening(), 8)

    async def settle(self) -> None:
        # Run Xlib outside the supervisor: an unresponsive display must not stop lease expiry.
        command = [sys.executable, "-I", __file__, "release-input", "--root", str(self.root), "--display", self.display]
        if self.authority:
            command += ["--authority", self.authority]
        process = await self.spawn(*command)
        try:
            if await asyncio.wait_for(process.wait(), 3) != 0:
                raise Refused("input_cleanup_unconfirmed")
        except asyncio.TimeoutError:  # Python 3.10 guest compatibility.  # noqa: UP041
            raise Refused("input_cleanup_unconfirmed") from None
        finally:
            await self.terminate(process)

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            await self.reap()
            if type(request.get("protocol_version")) is not int or request.get("protocol_version") != 2:
                raise Refused("unsupported_protocol_version")
            operation = request.get("operation")
            if operation == "describe":
                return {
                    "protocol_version": 2,
                    "generation": self.generation,
                    "port": self.port,
                    "max_ttl_seconds": MAX_TTL,
                    "read_only_enforced": True,
                    "revocation_fences_grants": True,
                }
            if request.get("generation") != self.generation:
                raise Refused("stale_generation")
            if operation == "settle":
                if any(g.mode == "control" and g.state == "active" for g in self.grants.values()):
                    raise Refused("controller_exists")
                await self.settle()
                return {"generation": self.generation, "input_cleanup_complete": True}
            if operation == "grant":
                return await self.grant(request)
            if operation == "ensure":
                return await self.ensure(request)
            identity = request.get("id")
            if not isinstance(identity, str):
                raise Refused("invalid_grant_id")
            grant = self.grants.get(identity)
            if operation == "revoke_browser":
                identity, episode = self.browser_fence(request, revoke=True)
                if self.browser_grants.get(identity) == episode:
                    assert grant is not None
                    await self.revoke(grant)
                    del self.grants[identity]
                    del self.browser_grants[identity]
                return {"id": identity, "generation": self.generation, "state": "revoked"}
            if grant is None:
                epoch = request.get("epoch")
                if (
                    operation == "revoke"
                    and re.fullmatch(r"[a-z][a-z0-9_]{7,71}", identity)
                    and type(epoch) is int
                    and epoch >= 1
                ):
                    if identity not in self.revocations and len(self.grants) + len(self.revocations) >= MAX_HISTORY:
                        raise Refused("capacity_exceeded")
                    if self.revocations.get(identity, epoch) != epoch:
                        raise Refused("stale_epoch")
                    # Fence a grant still in flight from a lost/expired Host operation.
                    self.revocations[identity] = epoch
                    return {
                        "id": identity,
                        "generation": self.generation,
                        "mode": "control",
                        "epoch": epoch,
                        "state": "revoked",
                        "remaining_seconds": 0,
                    }
                raise Refused("grant_not_found")
            if request.get("epoch") != grant.epoch:
                raise Refused("stale_epoch")
            if operation == "revoke":
                await self.revoke(grant)
            elif operation == "renew":
                self.extend(grant, request)
            elif operation != "inspect":
                raise Refused("unsupported_operation")
            return grant.public(self.generation)

    async def connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def respond() -> dict[str, Any]:
            raw = await reader.readline()
            if len(raw) > MAX_MESSAGE:
                raise Refused("request_too_large")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise Refused("invalid_request")
            return {"result": await self.dispatch(request)}

        try:
            response = await asyncio.wait_for(respond(), 15)
        except Refused as error:
            response = {"error": str(error)}
        except Exception:
            response = {"error": "operation_unconfirmed"}
        try:
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve(self) -> None:
        import fcntl

        private_directory(self.root)
        with (self.root / "lock").open("w") as lock, control_address(self.root) as endpoint:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("already_running") from None
            address = self.root / "control.sock"
            address.unlink(missing_ok=True)
            self.tokens()
            self.proxy_process = await self.spawn(
                self.proxy,
                "--token-plugin",
                "TokenFile",
                "--token-source",
                str(self.root / "tokens"),
                f"0.0.0.0:{self.port}",
            )
            try:
                # Do not advertise management readiness while Docker can only reach
                # its port-forwarding listener and the WebSocket backend is absent.
                await self.wait_ready(self.proxy_process, self.port)
                async with await asyncio.start_unix_server(self.connection, path=endpoint, limit=MAX_MESSAGE):
                    publish(self.root / "generation", self.generation)
                    loop = asyncio.get_running_loop()
                    for signum in (signal.SIGTERM, signal.SIGINT):
                        loop.add_signal_handler(signum, self.stop.set)
                    while not self.stop.is_set() and self.proxy_process.returncode is None:
                        async with self.lock:
                            try:
                                await self.reap()
                            except Refused:
                                # Keep the admin endpoint available to reconcile blocked cleanup.
                                pass
                        try:
                            await asyncio.wait_for(self.stop.wait(), 0.1)
                        except asyncio.TimeoutError:  # Python 3.10 guest compatibility.  # noqa: UP041
                            pass
            finally:
                try:
                    async with self.lock:
                        for grant in self.grants.values():
                            try:
                                await self.revoke(grant)
                            except Refused:
                                pass
                finally:
                    await self.terminate(self.proxy_process)
                    address.unlink(missing_ok=True)


def release_input(display_name: str, authority: str | None) -> None:
    """Release held input only after exclusive managed execution has been drained.

    This is whole-display cleanup, not a per-application/hostile-guest permission fence.
    XSync confirms our release requests; it does not prove other X clients or application work are quiescent.
    """
    if authority:
        os.environ["XAUTHORITY"] = authority
    x = ctypes.CDLL("libX11.so.6")
    xtest = ctypes.CDLL("libXtst.so.6")
    pointer, integer, window = ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong
    x.XOpenDisplay.argtypes, x.XOpenDisplay.restype = [ctypes.c_char_p], pointer
    x.XDefaultRootWindow.argtypes, x.XDefaultRootWindow.restype = [pointer], window
    x.XQueryKeymap.argtypes = [pointer, ctypes.c_void_p]
    x.XQueryPointer.argtypes = [
        pointer,
        window,
        *([ctypes.POINTER(window)] * 2),
        *([ctypes.POINTER(integer)] * 4),
        ctypes.POINTER(ctypes.c_uint),
    ]
    x.XSync.argtypes, x.XCloseDisplay.argtypes = [pointer, integer], [pointer]
    xtest.XTestFakeKeyEvent.argtypes = [pointer, ctypes.c_uint, integer, ctypes.c_ulong]
    xtest.XTestFakeButtonEvent.argtypes = [pointer, ctypes.c_uint, integer, ctypes.c_ulong]
    display = x.XOpenDisplay(display_name.encode())
    if not display:
        raise Refused("display_unavailable")
    try:
        keys = ctypes.create_string_buffer(32)
        x.XQueryKeymap(display, keys)
        for keycode in range(8, 256):
            if keys.raw[keycode // 8] & (1 << (keycode % 8)):
                if not xtest.XTestFakeKeyEvent(display, keycode, 0, 0):
                    raise Refused("input_cleanup_unconfirmed")
        # Release all core buttons, including wheel buttons; no other writer is admitted here.
        for button in range(1, 10):
            if not xtest.XTestFakeButtonEvent(display, button, 0, 0):
                raise Refused("input_cleanup_unconfirmed")
        x.XSync(display, 0)
        x.XQueryKeymap(display, keys)
        root, child, mask = window(), window(), ctypes.c_uint()
        coordinates = [integer() for _ in range(4)]
        if not x.XQueryPointer(
            display,
            x.XDefaultRootWindow(display),
            ctypes.byref(root),
            ctypes.byref(child),
            *(ctypes.byref(value) for value in coordinates),
            ctypes.byref(mask),
        ):
            raise Refused("input_cleanup_unconfirmed")
        if any(keys.raw) or mask.value & 0x1F00:
            raise Refused("input_cleanup_unconfirmed")
    finally:
        x.XCloseDisplay(display)


async def call(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    async def exchange() -> dict[str, Any]:
        with control_address(root) as endpoint:
            reader, writer = await asyncio.open_unix_connection(endpoint, limit=MAX_MESSAGE)
        try:
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()
            return json.loads(await reader.readline())
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.wait_for(exchange(), 20)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("serve", "call", "release-input"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--request")
    parser.add_argument("--display", default=":0")
    parser.add_argument("--authority")
    parser.add_argument("--port", type=int, default=6081)
    parser.add_argument("--proxy", default="websockify")
    args = parser.parse_args()
    try:
        if args.operation == "serve":
            asyncio.run(Supervisor(Path(args.root), args.display, args.authority, args.port, args.proxy).serve())
        elif args.operation == "release-input":
            release_input(args.display, args.authority)
        else:
            request = json.loads(base64.b64decode(args.request, validate=True))
            print(json.dumps(asyncio.run(call(Path(args.root), request))))
    except Exception:
        print(json.dumps({"error": "endpoint_unavailable"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
