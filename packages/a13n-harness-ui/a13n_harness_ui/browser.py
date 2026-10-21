"""Linux Host browser, isolated display, and server-enforced VNC access."""

from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from a13n_harness.capabilities.browser import (
    BrowserAction,
    BrowserElement,
    BrowserError,
    BrowserHumanControl,
    BrowserObservation,
)
from pydantic import Field

from a13n_harness_ui.browser_auth import vnc_passwords, write_private, xauthority
from a13n_harness_ui.browser_cursor import Cursor
from a13n_harness_ui.browser_display import (
    settle_wayvnc,
    spawn,
    sway_command,
    sway_windows,
    terminate,
    wait_socket,
    wayland,
)
from a13n_harness_ui.browser_setup import BrowserSetup, VncExecutable, inspect_browser
from a13n_harness_ui.configuration.models import BrowserConfiguration
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import SurfaceModel

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, ElementHandle, Page


type BrowserPreset = Literal["desktop", "laptop", "wide", "tablet", "tablet-landscape", "phone", "phone-landscape"]


class BrowserViewport(SurfaceModel):
    preset: BrowserPreset
    label: str
    width: int
    height: int


VIEWPORTS = (
    BrowserViewport(preset="desktop", label="Desktop", width=1280, height=800),
    BrowserViewport(preset="laptop", label="Laptop", width=1366, height=768),
    BrowserViewport(preset="wide", label="Desktop HD", width=1920, height=1080),
    BrowserViewport(preset="tablet", label="Tablet", width=768, height=1024),
    BrowserViewport(preset="tablet-landscape", label="Tablet landscape", width=1024, height=768),
    BrowserViewport(preset="phone", label="Phone", width=390, height=844),
    BrowserViewport(preset="phone-landscape", label="Phone landscape", width=844, height=390),
)
VIEWPORT_BY_PRESET = {viewport.preset: viewport for viewport in VIEWPORTS}
FIXED_DESKTOP = (1366, 1024)


class BrowserState(SurfaceModel):
    session_id: str = ""
    status: Literal["disabled", "enabled", "ready", "stopped", "failed"] = "disabled"
    control: Literal["agent", "human"] = "agent"
    url: str = ""
    error: str | None = None
    viewport: BrowserPreset = "desktop"
    viewports: tuple[BrowserViewport, ...] = VIEWPORTS
    resize_available: bool = False


class BrowserCommand(SurfaceModel):
    session_id: str = Field(min_length=1, max_length=80)


class BrowserAccessRequest(BrowserCommand):
    control_token: str = Field(default="", max_length=80, repr=False)


class BrowserViewportRequest(BrowserAccessRequest):
    preset: BrowserPreset


class BrowserAccess(SurfaceModel):
    ticket: str
    control: bool
    password: str = Field(repr=False)


class BrowserControl(SurfaceModel):
    state: BrowserState
    control_token: str = Field(repr=False)


class HostBrowserError(HarnessUiError, BrowserError):
    pass


class HumanBrowserControl(HarnessUiError, BrowserHumanControl):
    pass


def failure(message: str, code: str = "operation_failed") -> HarnessUiError:
    if code == "human_control":
        return HumanBrowserControl(message, code="browser_human_control")
    return HostBrowserError(message, code="browser_" + code)


def browser_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise failure("Use an HTTP(S) URL without embedded credentials.", "url_invalid")
    return value


async def require_browser(configuration: BrowserConfiguration) -> BrowserSetup:
    setup = await inspect_browser(configuration)
    if not setup.ready or setup.browser is None:
        raise failure(setup.message, "runtime_not_ready")
    return setup


async def close_context(context: BrowserContext) -> None:
    from playwright.async_api import Error

    try:
        await context.close()
    except Error:
        # Chromium can disconnect between the close request and its response.
        # Only an already disconnected browser makes that cleanup complete.
        if context.browser is None or context.browser.is_connected():
            raise


@dataclass
class BrowserWindow:
    binding: str = field(default_factory=lambda: "browser_" + secrets.token_hex(12))
    stopped: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    window_id: int | None = None
    page: Page | None = None
    revision: str = ""
    targets: dict[str, ElementHandle] = field(default_factory=dict)
    requires_observation: bool = False

    async def invalidate(self) -> None:
        self.revision = ""
        targets, self.targets = self.targets, {}
        await asyncio.gather(*(element.dispose() for element in targets.values()), return_exceptions=True)


class BrowserSession:
    def __init__(
        self,
        thread_id: str,
        profile: Path,
        *,
        viewport: BrowserPreset = "desktop",
        configuration: BrowserConfiguration | None = None,
    ) -> None:
        self.profile = profile
        self.active_thread = thread_id
        self.human_thread = ""
        self.windows: dict[str, BrowserWindow] = {}
        self.window_owners: dict[int, str] = {}
        self.native_windows: dict[int, int] = {}
        self.page_owners: dict[Page, str] = {}
        self.configuration = configuration or BrowserConfiguration()
        self.desktop: VncExecutable | None = None
        self.state = BrowserState(session_id="browser_" + secrets.token_hex(12), status="enabled", viewport=viewport)
        self.windows[thread_id] = BrowserWindow(binding=self.state.session_id)
        self.lock = asyncio.Lock()
        self.resources = AsyncExitStack()
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.keepalive: Page | None = None
        self.cursor = Cursor()
        self.env: dict[str, str] = {}
        self.display = ""
        self.control_socket = ""
        self.readonly_socket = ""
        self.password_file: Path | None = None
        self.password = ""
        self.readonly_password = ""
        self.control_token = ""
        self.released = asyncio.Event()
        self.released.set()
        self.connections: set[asyncio.StreamWriter] = set()
        self.connection_threads: dict[asyncio.StreamWriter, str] = {}
        self.controller_connection: asyncio.StreamWriter | None = None
        self.generation = 0
        self.retired = False
        self.monitors: set[asyncio.Task[None]] = set()
        self.cleanup: asyncio.Task[None] | None = None
        self.page_tasks: set[asyncio.Task[None]] = set()
        self.display_output = ""
        self.display_modes: set[str] = set()

    async def rotate_passwords(self) -> None:
        if self.password_file is None:
            return
        password, readonly = secrets.token_urlsafe(6), secrets.token_urlsafe(6)
        content = (
            f"{password}\n{readonly}\n".encode("ascii")
            if self.desktop is not None and self.desktop.kind == "x11vnc"
            else vnc_passwords(password, readonly)
        )
        await asyncio.to_thread(
            write_private,
            self.password_file,
            content,
            advance_mtime=self.desktop is not None and self.desktop.kind == "x11vnc",
        )
        self.password, self.readonly_password = password, readonly

    async def invalidate(self) -> None:
        for tab in self.windows.values():
            await tab.invalidate()
            tab.requires_observation = True

    async def window_id(self, page: Page) -> int:
        assert self.context is not None
        cdp = await self.context.new_cdp_session(page)
        try:
            return (await cdp.send("Browser.getWindowForTarget"))["windowId"]
        finally:
            if not page.is_closed():
                await cdp.detach()

    async def native_window(self, page: Page, before: set[int] | None = None) -> int:
        identity = await self.window_id(page)
        if identity in self.native_windows:
            return self.native_windows[identity]
        async with asyncio.timeout(5):
            while True:
                windows = await sway_windows(self.env)
                candidates = set(windows) - (before if before is not None else set(self.native_windows.values()))
                if len(candidates) > 1:
                    title = await page.title()
                    candidates = {
                        key for key in candidates if windows[key] == title or windows[key].startswith(title + " - ")
                    }
                if len(candidates) == 1:
                    native = candidates.pop()
                    self.native_windows[identity] = native
                    return native
                await asyncio.sleep(0.025)

    async def focus(self, page: Page) -> None:
        await page.bring_to_front()
        if self.desktop is not None and self.desktop.kind == "wayvnc":
            await sway_command(self.env, await self.native_window(page), "focus")

    async def open_window(self, thread_id: str) -> BrowserWindow:
        window = self.windows[thread_id]
        if window.stopped:
            raise failure("Browser window stopped. Start it before acting.", "session_conflict")
        if self.context is not None and (window.page is None or window.page.is_closed()):
            try:
                assert self.keepalive is not None
                self.active_thread = thread_id
                before = (
                    set(await sway_windows(self.env))
                    if self.desktop is not None and self.desktop.kind == "wayvnc"
                    else None
                )
                cdp = await self.context.new_cdp_session(self.keepalive)
                try:
                    async with self.context.expect_page() as opened:
                        await cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True})
                    page = await opened.value
                finally:
                    await cdp.detach()
                window.window_id = await self.window_id(page)
                if before is not None:
                    await self.native_window(page, before)
                self.window_owners[window.window_id] = thread_id
                self.page_owners[page] = thread_id
                window.page = self.page = page
                await self.layout(page, self.state.viewport)
                await self.fit_display()
                await self.focus(page)
            except Exception as exc:
                await self.close_window(thread_id)
                raise failure(
                    "The browser window could not open. Start it again after checking the Host display.",
                    "window_unavailable",
                ) from exc
        return window

    def retire_window(self, thread_id: str) -> BrowserWindow:
        window = self.windows[thread_id]
        window.closed.set()
        replacement = BrowserWindow(stopped=True, requires_observation=True)
        self.windows[thread_id] = replacement
        self.native_windows = {
            key: native for key, native in self.native_windows.items() if self.window_owners.get(key) != thread_id
        }
        self.window_owners = {key: owner for key, owner in self.window_owners.items() if owner != thread_id}
        if self.human_thread == thread_id:
            self.control_token = ""
        for writer, owner in tuple(self.connection_threads.items()):
            if owner == thread_id:
                writer.close()
        return window

    async def close_window(self, thread_id: str, window: BrowserWindow | None = None) -> None:
        window = window or self.retire_window(thread_id)
        try:
            if self.human_thread == thread_id:
                await self.revoke()
                self.human_thread = ""
                self.state = self.state.model_copy(update={"control": "agent"})
                self.released.set()
                await self.invalidate()
            # Keep the runtime's internal window alive even when the final
            # conversation closes, so Chromium retains its profile and process.
            while pages := [page for page, owner in self.page_owners.items() if owner == thread_id]:
                for page in pages:
                    if not page.is_closed():
                        await page.close(run_before_unload=False)
                    self.page_owners.pop(page, None)
            self.window_owners = {key: owner for key, owner in self.window_owners.items() if owner != thread_id}
        finally:
            await window.invalidate()

    def retire(self) -> None:
        self.retired = True
        self.generation += 1
        self.control_token = ""
        self.released.set()
        for window in self.windows.values():
            window.closed.set()
        for writer in tuple(self.connections):
            writer.close()

    def end(self, error: str | None = None) -> None:
        if self.retired:
            return
        # Revoke synchronously, before cleanup waits for an in-flight operation.
        self.retire()
        self.state = self.state.model_copy(update={"status": "stopped", "control": "agent", "url": "", "error": error})
        self.cleanup = asyncio.create_task(self.close_resources())

    def page_closed(self, page: Page) -> None:
        if self.retired:
            return
        if page is self.keepalive:
            self.end("The browser runtime window closed. Start the browser to continue.")
            return
        owner = self.page_owners.pop(page, None)
        if owner is not None:
            window = self.windows[owner]
            pages = [
                candidate
                for candidate, thread in self.page_owners.items()
                if thread == owner and not candidate.is_closed()
            ]
            if not pages and not window.stopped:
                window = self.retire_window(owner)
                task = asyncio.create_task(self.finish_closed_window(owner, window))
                self.page_tasks.add(task)
                task.add_done_callback(self.page_tasks.discard)
            elif window.page is page:
                window.page = pages[-1] if pages else None
                window.revision = ""
                window.requires_observation = True
        if self.page is page:
            self.page = self.keepalive

    async def finish_closed_window(self, thread_id: str, window: BrowserWindow) -> None:
        async with self.lock:
            await window.invalidate()
            if window.page is not None and not window.page.is_closed():
                await window.page.close(run_before_unload=False)
            if self.human_thread == thread_id:
                await self.revoke()
                self.human_thread = ""
                self.state = self.state.model_copy(update={"control": "agent"})
                self.released.set()
                await self.invalidate()

    def watch_page(self, page: Page) -> None:
        page.set_default_timeout(10_000)
        page.on("close", lambda _: self.page_closed(page))
        page.on("crash", lambda _: self.page_closed(page))

        def popup_owner(popup: Page) -> None:
            self.page_owners.setdefault(popup, self.page_owners.get(page, self.active_thread))

        page.on("popup", popup_owner)
        if self.state.status == "ready":
            task = asyncio.create_task(self.prepare_page(page, self.human_thread or self.active_thread))
            self.page_tasks.add(task)
            task.add_done_callback(self.page_tasks.discard)

    async def randr(self, *args: str) -> str:
        if self.desktop is None or self.desktop.xrandr is None:
            raise failure("Desktop resizing is unavailable on this Host.", "resize_unavailable")
        process = await asyncio.create_subprocess_exec(
            self.desktop.xrandr, *args, env=self.env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5)
        except BaseException:
            await terminate(process)
            raise
        if process.returncode != 0:
            raise failure("The virtual desktop could not resize.", "resize_failed")
        return output.decode(errors="replace")

    async def layout(self, page: Page, preset: BrowserPreset) -> None:
        assert self.context is not None
        viewport = VIEWPORT_BY_PRESET[preset]
        cdp = await self.context.new_cdp_session(page)
        try:
            window = await cdp.send("Browser.getWindowForTarget")
            await cdp.send(
                "Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}}
            )
            await page.set_viewport_size({"width": viewport.width, "height": viewport.height})
            await cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"left": 0, "top": 0}})
        finally:
            if not page.is_closed():
                await cdp.detach()

    async def fit_display(self) -> None:
        assert self.context is not None
        width = height = 32
        # Keep the full desktop large enough for other browser windows, including
        # login popups with different amounts of browser chrome.
        for page in self.context.pages:
            if page.is_closed() or page is self.keepalive:
                continue
            cdp = await self.context.new_cdp_session(page)
            try:
                window = await cdp.send("Browser.getWindowForTarget")
                width = max(width, window["bounds"]["width"])
                height = max(height, window["bounds"]["height"])
            finally:
                if not page.is_closed():
                    await cdp.detach()
        if not 32 <= width <= 4096 or not 32 <= height <= 4096:
            raise failure("The browser window is outside supported desktop dimensions.", "resize_failed")
        if self.desktop is None or self.desktop.xrandr is None:
            if width > FIXED_DESKTOP[0] or height > FIXED_DESKTOP[1]:
                raise failure(
                    "The browser window exceeds the fixed desktop size. Prepare desktop resizing support.",
                    "resize_unavailable",
                )
            return
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

    async def prepare_page(self, page: Page, owner_hint: str) -> None:
        async with self.lock:
            if self.retired or page.is_closed():
                return
            try:
                identity = await self.window_id(page)
                owner = self.page_owners.get(page) or self.window_owners.get(identity)
                if owner is None:
                    opener = await page.opener()
                    owner = self.page_owners.get(opener, owner_hint) if opener is not None else owner_hint
                if owner not in self.windows or self.windows[owner].stopped:
                    await page.close(run_before_unload=False)
                    return
                self.window_owners[identity] = owner
                self.page_owners[page] = owner
                window = self.windows[owner]
                if window.page is not page:
                    await window.invalidate()
                    window.page = page
                    window.requires_observation = True
                await self.layout(page, self.state.viewport)
                await self.fit_display()
            except Exception:
                if not self.retired and not page.is_closed():
                    self.end("Browser sizing failed. Start the browser to continue.")

    def watch_process(self, process: asyncio.subprocess.Process, name: str) -> None:
        async def watch() -> None:
            await process.wait()
            self.end(f"{name} exited. Start the browser to continue with its saved profile.")

        task = asyncio.create_task(watch())
        self.monitors.add(task)
        task.add_done_callback(self.monitors.discard)

    async def start(self) -> None:
        if self.retired:
            raise failure("Browser session changed. Start the browser before acting.", "session_conflict")
        if self.context is not None:
            return
        preparation = await require_browser(self.configuration)
        browser, self.desktop = preparation.browser, preparation.vnc
        assert browser is not None and self.desktop is not None
        try:
            from playwright.async_api import async_playwright

            await asyncio.to_thread(self.profile.mkdir, parents=True, exist_ok=True, mode=0o700)
            temporary = self.resources.enter_context(tempfile.TemporaryDirectory(prefix="a13n-browser-"))
            processes: list[tuple[asyncio.subprocess.Process, str]] = []
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
                    "-localhost",
                    "yes",
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
                    raise failure("The virtual display did not provide a display number.", "runtime_unavailable")
                self.display = ":" + number
                await asyncio.to_thread(write_private, authority, xauthority(number, cookie))
                self.env = {**os.environ, "DISPLAY": self.display, "XAUTHORITY": str(authority)}
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
                except (HarnessUiError, OSError, TimeoutError):
                    self.display_output = ""
                if not self.display_output:
                    self.desktop = self.desktop.model_copy(update={"xrandr": None})
            if self.desktop.xrandr is None:
                self.state = self.state.model_copy(update={"viewport": "desktop"})
            manager = None
            if self.desktop.window_manager:
                manager = await spawn(self.desktop.window_manager, env=self.env)
                self.resources.push_async_callback(terminate, manager)
            driver = await self.resources.enter_async_context(async_playwright())
            self.context = await driver.chromium.launch_persistent_context(
                str(self.profile),
                executable_path=browser.path,
                headless=False,
                env={**self.env},
                viewport=None,
                args=[
                    "--window-position=0,0",
                    f"--ozone-platform={'wayland' if self.desktop.kind == 'wayvnc' else 'x11'}",
                ],
                accept_downloads=False,
            )
            if self.context.browser is not None:
                # Context close events can precede Chromium process exit. Wait for
                # the browser before stopping its driver or releasing the profile.
                self.resources.push_async_callback(self.context.browser.close)
            self.resources.push_async_callback(close_context, self.context)
            self.context.on("close", lambda _: self.end())
            self.context.on("page", self.watch_page)
            for page in self.context.pages:
                self.watch_page(page)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.keepalive = self.page
            await self.keepalive.evaluate("document.title = 'A13n Harness browser runtime'")
            if self.desktop is not None and self.desktop.kind == "wayvnc":
                await sway_command(self.env, await self.native_window(self.keepalive), "move scratchpad")
            cdp = await self.context.new_cdp_session(self.keepalive)
            try:
                identity = await cdp.send("Browser.getWindowForTarget")
                await cdp.send(
                    "Browser.setWindowBounds",
                    {"windowId": identity["windowId"], "bounds": {"windowState": "minimized"}},
                )
            finally:
                await cdp.detach()
            if self.retired or self.keepalive.is_closed():
                raise failure("Chromium closed during startup. Start the browser to try again.", "runtime_unavailable")
            self.state = self.state.model_copy(
                update={"status": "ready", "error": None, "resize_available": self.desktop.xrandr is not None}
            )
            for process, name in processes:
                self.watch_process(process, name)
            if manager is not None:
                self.watch_process(manager, "The window manager")
        except BaseException as exc:
            self.retire()
            # start() already owns the operation lock.
            await self.clear_resources()
            if not isinstance(exc, Exception):
                raise
            message = (
                str(exc)
                if isinstance(exc, HarnessUiError)
                else "Browser startup failed. Check the configured executables and display dependencies, and ensure the shared profile is not open in another process. WayVNC with Sway requires a non-root Host process."
            )
            self.state = self.state.model_copy(update={"status": "failed", "control": "agent", "error": message})
            raise failure(message, "runtime_unavailable") from exc

    async def revoke(self) -> None:
        self.generation += 1
        self.control_token = ""
        self.controller_connection = None
        # Closing the backend sockets revokes established WebSockets, including quiet viewers.
        for writer in tuple(self.connections):
            writer.close()
        if not self.retired:
            try:
                await self.rotate_passwords()
                if self.readonly_socket:
                    await settle_wayvnc(Path(self.control_socket).with_suffix(".ctl"))
                if self.desktop is not None and self.desktop.kind == "x11vnc" and self.page is not None:
                    # Unlike TigerVNC, x11vnc retains global X11 key state after
                    # a client disconnect. Use its native synchronous control
                    # command before allowing the Agent to resume.
                    reset = await spawn(self.desktop.path, "-norc", "-sync", "-R", "clear_all", env=self.env)
                    try:
                        await asyncio.wait_for(reset.wait(), 5)
                        if reset.returncode != 0:
                            raise RuntimeError("x11vnc could not release held keys")
                    finally:
                        await terminate(reset)
            except Exception:
                self.end("VNC input authority could not be renewed. Start the browser to continue.")
                raise

    async def clear_resources(self) -> None:
        await self.cursor.hide()
        monitors = (*self.monitors, *self.page_tasks)
        for task in monitors:
            task.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        await self.revoke()
        self.connections.clear()
        await self.invalidate()
        try:
            await self.resources.aclose()
        finally:
            self.resources = AsyncExitStack()
            self.context = self.page = self.keepalive = None
            self.window_owners.clear()
            self.native_windows.clear()
            self.connection_threads.clear()
            self.page_owners.clear()
            for tab in self.windows.values():
                tab.page = None
            self.control_socket = self.readonly_socket = ""
            self.password_file = None
            self.password = self.readonly_password = ""
            self.env = {}
            self.display = ""
            self.display_output = ""
            self.display_modes.clear()

    async def close_resources(self) -> None:
        async with self.lock:
            await self.clear_resources()

    async def close(self) -> None:
        self.retire()
        if self.cleanup is None:
            self.cleanup = asyncio.create_task(self.close_resources())
        await asyncio.shield(self.cleanup)


class HostBrowsers:
    def __init__(
        self,
        *,
        root: Path,
        available: bool,
        authorize: Callable[[str], Awaitable[None]],
        configuration: BrowserConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or BrowserConfiguration()
        self.root = Path(self.configuration.profile_root) if self.configuration.profile_root else root / "profile"
        self.available, self.authorize = available, authorize
        # Enabled Thread bindings borrow one runtime; these values are aliases.
        self.sessions: dict[str, BrowserSession] = {}
        self.tickets: dict[str, tuple[str, str, int, bool, float]] = {}
        self.lifecycle = asyncio.Lock()

    def state(self, thread_id: str) -> BrowserState:
        session = self.sessions.get(thread_id)
        if session and (page := session.windows[thread_id].page) is not None and page.is_closed():
            session.page_closed(page)
        if session is None:
            return BrowserState()
        tab = session.windows[thread_id]
        return session.state.model_copy(
            update={
                "session_id": tab.binding,
                "status": "stopped"
                if tab.stopped
                else ("enabled" if session.state.status == "ready" and tab.page is None else session.state.status),
                "url": tab.page.url if tab.page is not None and not tab.page.is_closed() else "",
            }
        )

    def binding(self, thread_id: str) -> str | None:
        session = self.sessions.get(thread_id)
        return (
            session.windows[thread_id].binding
            if session and not session.retired and not session.windows[thread_id].stopped
            else None
        )

    def session(self, thread_id: str, binding: str) -> BrowserSession:
        session = self.sessions.get(thread_id)
        if (
            session is None
            or session.retired
            or session.windows[thread_id].stopped
            or session.windows[thread_id].binding != binding
        ):
            raise failure("Browser session changed. Refresh before acting.", "session_conflict")
        return session

    async def setup(self, thread_id: str) -> BrowserSetup:
        await self.authorize(thread_id)
        result = await inspect_browser(self.configuration)
        await self.authorize(thread_id)
        return result

    async def enable(self, thread_id: str) -> BrowserState:
        await self.authorize(thread_id)
        async with self.lifecycle:
            if thread_id not in self.sessions:
                await require_browser(self.configuration)
                await self.authorize(thread_id)
                runtime = next(iter(self.sessions.values()), None)
                if runtime is None:
                    runtime = BrowserSession(thread_id, self.root, configuration=self.configuration)
                async with runtime.lock:
                    runtime.windows.setdefault(thread_id, BrowserWindow(requires_observation=runtime.retired))
                    self.sessions[thread_id] = runtime
            return self.state(thread_id)

    async def replace(self, session: BrowserSession) -> BrowserSession:
        await session.close()
        threads = [thread_id for thread_id, runtime in self.sessions.items() if runtime is session]
        replacement = BrowserSession(
            threads[0], session.profile, viewport=session.state.viewport, configuration=self.configuration
        )
        replacement.windows = {
            thread_id: BrowserWindow(stopped=True, requires_observation=True) for thread_id in threads
        }
        for thread_id in threads:
            self.sessions[thread_id] = replacement
        return replacement

    async def start(self, thread_id: str, binding: str, *, explicit: bool = False) -> BrowserState:
        async with self.lifecycle:
            await self.authorize(thread_id)
            session = self.sessions.get(thread_id)
            if session is None or session.windows[thread_id].binding != binding:
                raise failure("Browser session changed. Refresh before acting.", "session_conflict")
            if session.retired:
                if not explicit:
                    raise failure("Browser session changed. Start the browser before acting.", "session_conflict")
                session = await self.replace(session)
            async with session.lock:
                if session.state.control == "human":
                    raise failure("The user controls the browser. Ask for handback before continuing.", "human_control")
                if session.windows[thread_id].stopped:
                    if not explicit:
                        raise failure("Browser session changed. Start the browser before acting.", "session_conflict")
                    session.windows[thread_id] = BrowserWindow(requires_observation=True)
                await session.start()
                await session.open_window(thread_id)
            return self.state(thread_id)

    async def reset(self, thread_id: str, binding: str) -> BrowserState:
        async with self.lifecycle:
            await self.authorize(thread_id)
            session = self.session(thread_id, binding)
            if session.state.control == "human" and session.human_thread != thread_id:
                raise failure("Hand back control before resetting another browser window.", "human_control")
            window = session.retire_window(thread_id)
            async with session.lock:
                await session.close_window(thread_id, window)
                session.windows[thread_id] = BrowserWindow(requires_observation=True)
                await session.start()
                await session.open_window(thread_id)
            return self.state(thread_id)

    async def resize(self, thread_id: str, request: BrowserViewportRequest) -> BrowserState:
        await self.authorize(thread_id)
        session = self.session(thread_id, request.session_id)
        async with session.lock:
            await self.authorize(thread_id)
            self.session(thread_id, request.session_id)
            if (
                session.state.control != "human"
                or session.human_thread != thread_id
                or not request.control_token
                or not secrets.compare_digest(request.control_token, session.control_token)
            ):
                raise failure("Take control before changing the browser viewport.", "control_required")
            if session.context is None:
                raise failure("Start the browser before changing its viewport.", "not_ready")
            if not session.state.resize_available:
                raise failure("Desktop resizing is unavailable on this Host.", "resize_unavailable")
            if request.preset == session.state.viewport:
                return self.state(thread_id)
            try:
                await session.invalidate()
                for page in session.context.pages:
                    if not page.is_closed() and page is not session.keepalive:
                        await session.layout(page, request.preset)
                await session.fit_display()
                self.session(thread_id, request.session_id)
                session.state = session.state.model_copy(update={"viewport": request.preset})
            except Exception as exc:
                session.end("Browser resizing failed. Start the browser to continue.")
                raise failure("Browser resizing failed. Start the browser to continue.", "resize_failed") from exc
            return self.state(thread_id)

    async def stop(self, thread_id: str, binding: str) -> BrowserState:
        async with self.lifecycle:
            await self.authorize(thread_id)
            session = self.sessions.get(thread_id)
            if session is None or session.windows[thread_id].binding != binding:
                raise failure("Browser session changed. Refresh before acting.", "session_conflict")
            window = session.retire_window(thread_id)
            async with session.lock:
                await session.close_window(thread_id, window)
            return self.state(thread_id)

    async def control(self, thread_id: str, binding: str, *, human: bool) -> BrowserControl:
        session = self.session(thread_id, binding)
        # Mark the barrier before waiting for an in-flight operation; queued Agent calls fail.
        await self.authorize(thread_id)
        if human and session.state.control == "human":
            raise failure("The browser is already under human control.", "control_conflict")
        if human and session.windows[thread_id].page is None:
            raise failure("Start the browser before taking control.", "not_ready")
        session.state = session.state.model_copy(update={"control": "human"})
        session.released.clear()
        async with session.lock:
            await self.authorize(thread_id)
            self.session(thread_id, binding)
            await session.revoke()
            await session.invalidate()
            if human:
                await session.cursor.hide()
                page = session.windows[thread_id].page
                if page is None:
                    raise failure("Start the browser before taking control.", "not_ready")
                await session.focus(page)
                session.page = page
                session.human_thread = thread_id
                session.control_token = secrets.token_urlsafe(24)
            else:
                session.human_thread = ""
                session.state = session.state.model_copy(update={"control": "agent"})
                session.released.set()
            return BrowserControl(state=self.state(thread_id), control_token=session.control_token)

    async def access(self, thread_id: str, request: BrowserAccessRequest) -> BrowserAccess:
        await self.authorize(thread_id)
        session = self.session(thread_id, request.session_id)
        async with session.lock:
            await self.authorize(thread_id)
            self.session(thread_id, request.session_id)
            page = session.windows[thread_id].page
            if page is None:
                raise failure("Start the browser before opening its desktop.", "not_ready")
            controlling = (
                session.human_thread == thread_id
                and bool(request.control_token)
                and secrets.compare_digest(request.control_token, session.control_token)
            )
            if request.control_token and (not controlling or session.state.control != "human"):
                raise failure("Human input authority has been revoked.", "control_conflict")
            if session.state.control == "agent" or controlling:
                await session.focus(page)
            now = time.monotonic()
            self.tickets = {k: v for k, v in self.tickets.items() if v[4] > now}
            if len(self.tickets) >= 128:
                raise failure("Too many pending viewer connections.", "limit")
            ticket = secrets.token_urlsafe(24)
            self.tickets[ticket] = (thread_id, request.session_id, session.generation, controlling, now + 30)
            return BrowserAccess(
                ticket=ticket,
                control=controlling,
                password=session.password if controlling else session.readonly_password,
            )

    async def connect(
        self, thread_id: str, ticket: str
    ) -> tuple[BrowserSession, asyncio.StreamReader, asyncio.StreamWriter]:
        entry = self.tickets.pop(ticket, None)
        if entry is None or entry[0] != thread_id or entry[4] < time.monotonic():
            raise failure("Viewer access expired. Reconnect explicitly.", "access_expired")
        _, binding, generation, controlling, _ = entry
        await self.authorize(thread_id)
        session = self.session(thread_id, binding)
        if session.generation != generation:
            raise failure("Viewer access was revoked.", "access_expired")
        if len(session.connections) >= 8:
            raise failure("Too many browser viewers.", "limit")
        if controlling and session.controller_connection is not None:
            raise failure("A controller is already connected.", "control_conflict")
        reader, writer = await asyncio.open_unix_connection(
            session.readonly_socket if not controlling and session.readonly_socket else session.control_socket
        )
        if session.generation != generation or session.retired or session.windows[thread_id].binding != binding:
            writer.close()
            raise failure("Viewer access was revoked.", "access_expired")
        if len(session.connections) >= 8:
            writer.close()
            raise failure("Too many browser viewers.", "limit")
        session.connections.add(writer)
        session.connection_threads[writer] = thread_id
        if controlling:
            # Recheck after opening the socket: two concurrent tickets cannot create two controllers.
            if session.controller_connection is not None:
                session.connections.discard(writer)
                session.connection_threads.pop(writer, None)
                writer.close()
                raise failure("A controller is already connected.", "control_conflict")
            session.controller_connection = writer
        return session, reader, writer

    async def disconnect(self, session: BrowserSession, writer: asyncio.StreamWriter) -> None:
        session.connections.discard(writer)
        session.connection_threads.pop(writer, None)
        writer.close()
        if session.controller_connection is writer:
            async with session.lock:
                if session.controller_connection is writer:
                    # A lost controller does not silently resume Agent reads. Revoke input,
                    # clear remote pressed keys, and leave explicit handback available.
                    await session.revoke()

    def backend(self, thread_id: str, binding: str) -> HostBrowserBackend:
        return HostBrowserBackend(self, thread_id, binding)

    async def close(self) -> None:
        # Shutdown owns cleanup even after Thread authority has been revoked.
        # Retire every binding first, and attempt all cleanups if one fails.
        try:
            async with AsyncExitStack() as resources:
                for session in set(self.sessions.values()):
                    session.end()
                    resources.push_async_callback(session.close)
        finally:
            self.sessions.clear()
            self.tickets.clear()


class HostBrowserBackend:
    def __init__(self, browsers: HostBrowsers, thread_id: str, binding: str) -> None:
        self.browsers, self.thread_id, self.binding = browsers, thread_id, binding

    async def wait_for_handback(self) -> None:
        session = self.browsers.session(self.thread_id, self.binding)
        window = session.windows[self.thread_id]
        # A stopped window wakes its own queued tools even if a different
        # conversation still holds the global human-control barrier.
        while session.state.control == "human":
            waits = [asyncio.create_task(session.released.wait()), asyncio.create_task(window.closed.wait())]
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in waits:
                    task.cancel()
                await asyncio.gather(*waits, return_exceptions=True)
            await self.browsers.authorize(self.thread_id)
            self.browsers.session(self.thread_id, self.binding)

    async def checked(self) -> BrowserSession:
        await self.browsers.authorize(self.thread_id)
        session = self.browsers.session(self.thread_id, self.binding)
        if session.state.control == "human":
            raise failure("The user controls the browser. Ask for handback before continuing.", "human_control")
        tab = await session.open_window(self.thread_id)
        if session.context is not None:
            # Human-opened tabs and popups stay with their owning Thread. Never
            # adopt another Thread's foreground page merely because it has focus.
            for candidate in reversed(session.context.pages[-16:]):
                if candidate.is_closed() or session.page_owners.get(candidate) != self.thread_id:
                    continue
                try:
                    focused = await asyncio.wait_for(candidate.evaluate("document.hasFocus()"), 1)
                except Exception:
                    continue
                if focused:
                    if tab.page != candidate:
                        await tab.invalidate()
                        tab.page = candidate
                        tab.requires_observation = True
                    break
        if tab.page is None or tab.page.is_closed():
            raise failure("Browser is not running. Navigate to start it.", "not_ready")
        session.active_thread = self.thread_id
        session.page = tab.page
        if session.context is not None:
            await session.focus(tab.page)
        return session

    @asynccontextmanager
    async def operation(self) -> AsyncIterator[BrowserSession]:
        session = self.browsers.session(self.thread_id, self.binding)
        async with session.lock:
            try:
                yield await self.checked()
                self.browsers.session(self.thread_id, self.binding)
            except HarnessUiError:
                raise
            except Exception as exc:
                raise failure(
                    "Browser operation failed; its outcome may be uncertain. Observe before retrying, "
                    "or start the browser if it closed."
                ) from exc

    async def observe(self) -> BrowserObservation:
        async with self.operation() as session:
            tab = session.windows[self.thread_id]
            page = tab.page
            assert page is not None
            await tab.invalidate()
            tab.revision = secrets.token_hex(12)
            elements = []
            # Keep actual element handles, never infer a replacement target from a stale index.
            candidates = await page.locator(
                "a,button,input,textarea,select,[role=button],[contenteditable=true]"
            ).element_handles()
            for element in candidates[:256]:
                if not await element.is_visible():
                    await element.dispose()
                    continue
                target = secrets.token_hex(12)
                description = await element.evaluate(
                    "e => [e.tagName.toLowerCase(), e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.innerText || e.getAttribute('name') || e.getAttribute('type') || ''].join(' ').slice(0,240)"
                )
                tab.targets[target] = element
                elements.append(BrowserElement(target, description))
            for element in candidates[256:]:
                await element.dispose()
            text = (await page.locator("body").aria_snapshot())[:24_000]
            tab.requires_observation = False
            session.state = session.state.model_copy(update={"url": page.url})
            return BrowserObservation(f"URL: {page.url}\n{text}", tab.revision, tuple(elements))

    async def act(self, action: BrowserAction, *, target: str = "", value: str = "", revision: str = "") -> str:
        if action == "navigate":
            browser_url(value)
            await self.browsers.start(self.thread_id, self.binding)
        async with self.operation() as session:
            tab = session.windows[self.thread_id]
            page = tab.page
            assert page is not None
            if tab.requires_observation:
                raise failure(
                    "Observe the browser after handback or reopening before continuing.", "observation_required"
                )
            try:
                if action == "navigate":
                    await tab.invalidate()
                    await page.goto(value, wait_until="domcontentloaded", timeout=20_000)
                elif action in {"click", "fill"}:
                    element = tab.targets.get(target)
                    if (
                        not revision
                        or revision != tab.revision
                        or element is None
                        or not await element.evaluate("e => e.isConnected")
                    ):
                        raise failure("Element reference expired. Observe again.", "reference_expired")
                    if action == "click":
                        await session.cursor.show(page, element)
                        await element.click()
                    else:
                        if await element.get_attribute("type") in {"password", "file"}:
                            raise failure("Use human takeover for password or file input.", "sensitive_input")
                        await session.cursor.show(page, element)
                        await element.fill(value)
                elif action == "press":
                    await session.cursor.show(page)
                    await page.keyboard.press(value)
                elif action == "wait":
                    await page.get_by_text(value, exact=False).first.wait_for(state="visible")
                session.state = session.state.model_copy(update={"url": page.url})
                return f"Browser {action} completed. Observe to verify the result."
            except HarnessUiError:
                raise
            except Exception as exc:
                raise failure("Browser action failed; its outcome may be uncertain. Observe before retrying.") from exc

    async def screenshot(self) -> bytes:
        async with self.operation() as session:
            tab = session.windows[self.thread_id]
            page = tab.page
            assert page is not None
            picture = await page.screenshot(type="jpeg", quality=65)
            if len(picture) > 3 * 1024 * 1024:
                raise failure("Screenshot exceeds the browser output limit.")
            return picture
