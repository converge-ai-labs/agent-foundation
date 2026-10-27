"""Managed private Wayland displays and readiness for local VNC sockets."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import stat
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path

from a13n_harness_ui.browser_auth import vnc_passwords, write_private, xauthority
from a13n_harness_ui.browser_setup import VncExecutable
from a13n_harness_ui.browser_vnc import VncConnection


async def terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            await process.wait()
            return
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def spawn(*args: str, env: dict[str, str] | None = None) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *args, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )


async def wait_socket(path: Path, process: asyncio.subprocess.Process) -> None:
    async with asyncio.timeout(10):
        while True:
            if process.returncode is not None:
                raise RuntimeError(f"Display component exited before creating {path.name}")
            try:
                if stat.S_ISSOCK(path.stat().st_mode):
                    path.chmod(0o600)
                    return
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.025)


async def wayland(
    desktop: VncExecutable, root: Path, resources: AsyncExitStack, size: tuple[int, int]
) -> tuple[dict[str, str], list[tuple[asyncio.subprocess.Process, str]]]:
    """Own a headless Sway compositor, never the user's existing desktop."""
    if os.geteuid() == 0:
        raise RuntimeError("WayVNC with Sway requires running Harness UI as a non-root user")
    assert desktop.display_server is not None
    config = root / "sway.conf"
    await asyncio.to_thread(
        write_private,
        config,
        (
            f"output HEADLESS-1 mode {size[0]}x{size[1]}\n"
            "xwayland disable\n"
            "default_border none\n"
            "focus_on_window_activation focus\n"
            "seat seat0 fallback true\n"
            'for_window [app_id=".*"] floating enable\n'
        ).encode(),
    )
    env = {key: value for key, value in os.environ.items() if key not in {"DISPLAY", "WAYLAND_DISPLAY", "SWAYSOCK"}}
    env.update(
        XDG_RUNTIME_DIR=str(root),
        WLR_BACKENDS="headless",
        WLR_HEADLESS_OUTPUTS="1",
        WLR_RENDERER="pixman",
        WLR_LIBINPUT_NO_DEVICES="1",
    )
    compositor = await spawn(desktop.display_server, "--config", str(config), env=env)
    resources.push_async_callback(terminate, compositor)
    async with asyncio.timeout(10):
        while True:
            if compositor.returncode is not None:
                raise RuntimeError("The headless Sway compositor exited during startup")
            sockets = [path for path in root.glob("wayland-*") if stat.S_ISSOCK(path.stat().st_mode)]
            controls = list(root.glob("sway-ipc.*.sock"))
            if sockets and controls:
                env["WAYLAND_DISPLAY"] = sockets[0].name
                env["SWAYSOCK"] = str(controls[0])
                break
            await asyncio.sleep(0.025)
    vnc_config = root / "wayvnc.conf"
    # No public listeners. Admission and authority belong to the authenticated
    # bridge; separate native endpoints enforce view-only input restrictions.
    await asyncio.to_thread(write_private, vnc_config, b"enable_auth=false\n")
    processes = [(compositor, "The Wayland compositor")]
    for readonly in (False, True):
        name = "readonly" if readonly else "desktop"
        arguments = [desktop.path, "--config", str(vnc_config), "--socket", str(root / f"{name}.ctl"), "--unix-socket"]
        if readonly:
            arguments.append("--disable-input")
        if desktop.disable_resizing:
            arguments.append("--disable-resizing")
        arguments.append(str(root / f"{name}.sock"))
        process = await spawn(*arguments, env=env)
        resources.push_async_callback(terminate, process)
        await wait_socket(root / f"{name}.sock", process)
        processes.append((process, f"The {'read-only' if readonly else 'control'} WayVNC server"))
    return env, processes


async def settle_wayvnc(socket_path: Path) -> None:
    """Wait for native client/keyboard destruction before admitting a controller."""
    async with asyncio.timeout(5):
        while True:
            reader, writer = await asyncio.open_unix_connection(socket_path)
            try:
                writer.write(b'{"method":"client-list","id":1}')
                await writer.drain()
                output = bytearray()
                while True:
                    chunk = await reader.read(4096)
                    if not chunk or len(output) + len(chunk) > 65536:
                        raise RuntimeError("Invalid WayVNC control response")
                    output.extend(chunk)
                    try:
                        response = json.loads(output)
                    except json.JSONDecodeError:
                        continue
                    if (
                        not isinstance(response, dict)
                        or response.get("code") != 0
                        or not isinstance(response.get("data"), list)
                    ):
                        raise RuntimeError("WayVNC could not report native client cleanup")
                    if not response["data"]:
                        return
                    break
            finally:
                writer.close()
                await writer.wait_closed()
            await asyncio.sleep(0.025)


async def sway_request(env: dict[str, str], kind: int, payload: bytes = b"") -> object:
    """Use Sway's native i3 IPC without requiring a swaymsg executable."""
    import struct

    async with asyncio.timeout(5):
        reader, writer = await asyncio.open_unix_connection(env["SWAYSOCK"])
        try:
            writer.write(b"i3-ipc" + struct.pack("<II", len(payload), kind) + payload)
            await writer.drain()
            header = await reader.readexactly(14)
            length, response_kind = struct.unpack("<II", header[6:])
            if header[:6] != b"i3-ipc" or response_kind != kind or length > 1024 * 1024:
                raise RuntimeError("Invalid Sway control response")
            return json.loads(await reader.readexactly(length))
        finally:
            writer.close()
            await writer.wait_closed()


async def sway_windows(env: dict[str, str]) -> dict[int, str]:
    def collect(node: object) -> dict[int, str]:
        if not isinstance(node, dict):
            raise RuntimeError("Invalid Sway window tree")
        windows = {}
        if node.get("app_id") and isinstance(node.get("id"), int) and isinstance(node.get("name"), str):
            windows[node["id"]] = node["name"]
        for child in [*node.get("nodes", []), *node.get("floating_nodes", [])]:
            windows.update(collect(child))
        return windows

    return collect(await sway_request(env, 4))


async def sway_command(env: dict[str, str], identity: int, command: str) -> None:
    # The only interpolated identity is an integer supplied by the private
    # compositor, never a website title or other executable command fragment.
    response = await sway_request(env, 0, f"[con_id={identity}] {command}".encode())
    if (
        not isinstance(response, list)
        or not response
        or any(not isinstance(item, dict) or item.get("success") is not True for item in response)
    ):
        raise RuntimeError("The compositor could not select the browser window")


FIXED_DESKTOP = (1366, 1024)


class VncDesktop:
    """One private graphical session; browser processes and access belong to the Host."""

    def __init__(self, desktop: VncExecutable) -> None:
        self.desktop = desktop
        self.resources = AsyncExitStack()
        self.env: dict[str, str] = {}
        self.display = ""
        self.control_socket = ""
        self.readonly_socket = ""
        self.password_file: Path | None = None
        self.password = self.readonly_password = ""
        self.display_output = ""
        self.display_modes: set[str] = set()
        self.processes: list[tuple[asyncio.subprocess.Process, str]] = []

    @property
    def wayland(self) -> bool:
        return self.desktop.kind == "wayvnc"

    @property
    def resize_available(self) -> bool:
        return self.desktop.xrandr is not None

    @property
    def window_manager(self) -> bool:
        return self.desktop.window_manager is not None

    @property
    def chromium_arguments(self) -> tuple[str, ...]:
        return ("--ozone-platform=wayland", "--disable-gpu") if self.wayland else ("--ozone-platform=x11",)

    async def connect(self, *, controlling: bool) -> VncConnection:
        path = self.readonly_socket if not controlling and self.readonly_socket else self.control_socket
        return VncConnection(*await asyncio.open_unix_connection(path))

    async def rotate_passwords(self) -> None:
        if self.password_file is None:
            return
        password, readonly = secrets.token_urlsafe(6), secrets.token_urlsafe(6)
        content = (
            f"{password}\n{readonly}\n".encode("ascii")
            if self.desktop.kind == "x11vnc"
            else vnc_passwords(password, readonly)
        )
        await asyncio.to_thread(write_private, self.password_file, content, advance_mtime=self.desktop.kind == "x11vnc")
        self.password, self.readonly_password = password, readonly

    async def start(self) -> None:
        processes: list[tuple[asyncio.subprocess.Process, str]] = []
        temporary = self.resources.enter_context(tempfile.TemporaryDirectory(prefix="a13n-browser-"))

        self.control_socket = str(Path(temporary) / "desktop.sock")
        if self.desktop.kind == "wayvnc":
            self.env, processes = await wayland(self.desktop, Path(temporary), self.resources, FIXED_DESKTOP)
            self.readonly_socket = str(Path(temporary) / "readonly.sock")
        else:
            authority = Path(temporary) / "Xauthority"
            cookie = secrets.token_bytes(16)
            await asyncio.to_thread(write_private, authority, xauthority("", cookie))
            self.control_socket = str(Path(temporary) / "desktop.sock")
            self.password_file = Path(temporary) / "passwd"
            await self.rotate_passwords()
            arguments = [
                self.desktop.path,
                "-displayfd",
                "1",
                "-s",
                "0",
                "-geometry",
                f"{FIXED_DESKTOP[0]}x{FIXED_DESKTOP[1]}",
                "-depth",
                "24",
                "-rfbport",
                "-1",
                "-rfbunixpath",
                self.control_socket,
                "-rfbunixmode",
                "0600",
                "-SecurityTypes",
                "VncAuth",
                "-PasswordFile",
                str(self.password_file),
                "-AlwaysShared",
                "-AcceptSetDesktopSize=0",
                "-SendPrimary=0",
                "-SetPrimary=0",
                "-MaxCutText",
                "262144",
                "-localhost=1",
                "-nolisten",
                "tcp",
                "-auth",
                str(authority),
            ]
            if self.desktop.kind == "x11vnc":
                assert self.desktop.display_server is not None
                arguments = [
                    self.desktop.display_server,
                    "-displayfd",
                    "1",
                    "-screen",
                    "0",
                    f"{FIXED_DESKTOP[0]}x{FIXED_DESKTOP[1]}x24",
                    "-nolisten",
                    "tcp",
                    "-auth",
                    str(authority),
                ]
            display = await asyncio.create_subprocess_exec(
                *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            self.resources.push_async_callback(terminate, display)
            assert display.stdout is not None
            number = (await asyncio.wait_for(display.stdout.readline(), 10)).strip().decode()
            if not number.isdecimal():
                raise RuntimeError("The virtual display did not provide a display number.")
            self.display = ":" + number
            await asyncio.to_thread(write_private, authority, xauthority(number, cookie))
            # This is a private X11 session even when the Host runs on Wayland.
            self.env = {key: value for key, value in os.environ.items() if key not in {"WAYLAND_DISPLAY", "SWAYSOCK"}}
            self.env.update(DISPLAY=self.display, XAUTHORITY=str(authority), XDG_SESSION_TYPE="x11")
            processes.append((display, "The virtual display"))
            if self.desktop.kind == "x11vnc":
                vnc = await spawn(
                    self.desktop.path,
                    "-norc",
                    "-display",
                    self.display,
                    "-auth",
                    str(authority),
                    "-unixsock",
                    self.control_socket,
                    "-rfbport",
                    "0",
                    "-passwdfile",
                    "read:" + str(self.password_file),
                    "-forever",
                    "-shared",
                    "-noprimary",
                    "-nosetprimary",
                    env=self.env,
                )
                self.resources.push_async_callback(terminate, vnc)
                await wait_socket(Path(self.control_socket), vnc)
                processes.append((vnc, "The x11vnc server"))
        if self.desktop.xrandr:
            try:
                outputs = (await self.randr("--query")).splitlines()
                self.display_output = next((line.split()[0] for line in outputs if " connected" in line), "")
            except (RuntimeError, OSError, TimeoutError):
                self.display_output = ""
            if not self.display_output:
                self.desktop = self.desktop.model_copy(update={"xrandr": None})
        manager = None
        if self.desktop.window_manager:
            manager = await spawn(self.desktop.window_manager, env=self.env)
            self.resources.push_async_callback(terminate, manager)
        if manager is not None:
            processes.append((manager, "The window manager"))
        self.processes = processes

    async def randr(self, *args: str) -> str:
        if self.desktop.xrandr is None:
            raise RuntimeError("Desktop resizing is unavailable on this Host.")
        process = await asyncio.create_subprocess_exec(
            self.desktop.xrandr, *args, env=self.env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5)
        except BaseException:
            await terminate(process)
            raise
        if process.returncode:
            raise RuntimeError("The virtual desktop could not resize.")
        return output.decode(errors="replace")

    async def resize(self, width: int, height: int) -> None:
        if self.desktop.xrandr is None:
            if width > FIXED_DESKTOP[0] or height > FIXED_DESKTOP[1]:
                raise RuntimeError("The browser window exceeds the fixed desktop size.")
            return
        if not 32 <= width <= 4096 or not 32 <= height <= 4096:
            raise RuntimeError("Unsupported desktop size.")
        mode = f"a13n-{width}x{height}"
        if mode not in self.display_modes:
            clock = (width + 24) * (height + 3) * 60 / 1_000_000
            await self.randr(
                "--newmode",
                mode,
                f"{clock:.6f}",
                *(
                    str(n)
                    for n in (width, width + 8, width + 16, width + 24, height, height + 1, height + 2, height + 3)
                ),
            )
            await self.randr("--addmode", self.display_output, mode)
            self.display_modes.add(mode)
        await self.randr("--output", self.display_output, "--mode", mode)

    async def settle_input(self) -> None:
        await self.rotate_passwords()
        if self.readonly_socket:
            await settle_wayvnc(Path(self.control_socket).with_suffix(".ctl"))
        elif self.desktop.kind == "x11vnc":
            reset = await spawn(self.desktop.path, "-norc", "-sync", "-R", "clear_all", env=self.env)
            try:
                await asyncio.wait_for(reset.wait(), 5)
                if reset.returncode:
                    raise RuntimeError("x11vnc could not release held keys")
            finally:
                await terminate(reset)

    async def close(self) -> None:
        await self.resources.aclose()
