"""Managed private Wayland displays and readiness for local VNC sockets."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from contextlib import AsyncExitStack
from pathlib import Path

from a13n_harness_ui.browser_auth import write_private
from a13n_harness_ui.browser_setup import VncExecutable


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
