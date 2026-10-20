"""Linux Host browser, isolated display, and server-enforced VNC access."""

from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
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

from a13n_harness_ui.browser_cursor import Cursor
from a13n_harness_ui.browser_install import BrowserInstaller
from a13n_harness_ui.browser_setup import BrowserExecutable, BrowserSetup, inspect_browser
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


class BrowserState(SurfaceModel):
    session_id: str = ""
    status: Literal["disabled", "enabled", "ready", "stopped", "failed"] = "disabled"
    control: Literal["agent", "human"] = "agent"
    url: str = ""
    error: str | None = None
    viewport: BrowserPreset = "desktop"
    viewports: tuple[BrowserViewport, ...] = VIEWPORTS


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


async def require_browser() -> BrowserExecutable:
    setup = await inspect_browser()
    if not setup.ready or setup.browser is None:
        raise failure(setup.message, "runtime_not_ready")
    return setup.browser


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


async def close_context(context: BrowserContext) -> None:
    from playwright.async_api import Error

    try:
        await context.close()
    except Error:
        # Chromium can disconnect between the close request and its response.
        # Only an already disconnected browser makes that cleanup complete.
        if context.browser is None or context.browser.is_connected():
            raise


class BrowserSession:
    def __init__(self, thread_id: str, profile: Path, *, viewport: BrowserPreset = "desktop") -> None:
        self.thread_id, self.profile = thread_id, profile
        self.state = BrowserState(session_id="browser_" + secrets.token_hex(12), status="enabled", viewport=viewport)
        self.lock = asyncio.Lock()
        self.resources = AsyncExitStack()
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.cursor = Cursor()
        self.env: dict[str, str] = {}
        self.display = ""
        self.control_socket = ""
        self.password_file: Path | None = None
        self.password = ""
        self.readonly_password = ""
        self.control_token = ""
        self.revision = ""
        self.targets: dict[str, ElementHandle] = {}
        self.requires_observation = False
        self.released = asyncio.Event()
        self.released.set()
        self.connections: set[asyncio.StreamWriter] = set()
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
        process = await asyncio.create_subprocess_exec(
            "tigervncpasswd",
            "-f",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            encoded, _ = await asyncio.wait_for(process.communicate(f"{password}\n{readonly}\n".encode()), 5)
        except BaseException:
            await terminate(process)
            raise
        if process.returncode != 0 or len(encoded) != 16:
            raise failure("VNC authentication could not be prepared.", "runtime_unavailable")
        path = self.password_file

        def publish() -> None:
            replacement = path.with_suffix(".new")
            replacement.touch(mode=0o600, exist_ok=False)
            replacement.write_bytes(encoded)
            replacement.replace(path)

        await asyncio.to_thread(publish)
        self.password, self.readonly_password = password, readonly

    async def invalidate(self) -> None:
        self.revision = ""
        targets, self.targets = self.targets, {}
        # A closed/crashed renderer may already have invalidated these handles.
        await asyncio.gather(*(element.dispose() for element in targets.values()), return_exceptions=True)

    def retire(self) -> None:
        self.retired = True
        self.generation += 1
        self.control_token = ""
        self.released.set()
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
        pages = [candidate for candidate in self.context.pages if not candidate.is_closed()] if self.context else []
        if not pages:
            self.end()
        elif self.page is page:
            self.page = pages[-1]
            self.revision = ""
            self.requires_observation = True

    def watch_page(self, page: Page) -> None:
        page.set_default_timeout(10_000)
        page.on("close", lambda _: self.page_closed(page))
        page.on("crash", lambda _: self.end("A browser page crashed. Start the browser to continue."))
        if self.state.status == "ready":
            task = asyncio.create_task(self.prepare_page(page))
            self.page_tasks.add(task)
            task.add_done_callback(self.page_tasks.discard)

    async def randr(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "xrandr", *args, env=self.env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
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
            if page.is_closed():
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

    async def prepare_page(self, page: Page) -> None:
        async with self.lock:
            if self.retired or page.is_closed():
                return
            try:
                await self.invalidate()
                self.requires_observation = True
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
        if self.page is not None and not self.page.is_closed():
            return
        browser = await require_browser()
        try:
            from playwright.async_api import async_playwright

            await asyncio.to_thread(self.profile.mkdir, parents=True, exist_ok=True, mode=0o700)
            temporary = self.resources.enter_context(tempfile.TemporaryDirectory(prefix="a13n-browser-"))
            authority = Path(temporary) / "Xauthority"
            authority.touch(mode=0o600)
            self.control_socket = str(Path(temporary) / "desktop.sock")
            self.password_file = Path(temporary) / "passwd"
            await self.rotate_passwords()
            display = await asyncio.create_subprocess_exec(
                "Xtigervnc",
                "-displayfd",
                "1",
                "-s",
                "0",
                "-geometry",
                "1280x800",
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
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.resources.push_async_callback(terminate, display)
            assert display.stdout is not None
            number = (await asyncio.wait_for(display.stdout.readline(), 10)).strip().decode()
            if not number.isdecimal():
                raise failure("The virtual display did not provide a display number.", "runtime_unavailable")
            self.display = ":" + number
            auth = await spawn("xauth", "-f", str(authority), "add", self.display, ".", secrets.token_hex(16))
            if await auth.wait() != 0:
                raise failure("Display authentication could not be prepared.", "runtime_unavailable")
            self.env = {**os.environ, "DISPLAY": self.display, "XAUTHORITY": str(authority)}
            outputs = (await self.randr("--query")).splitlines()
            self.display_output = next((line.split()[0] for line in outputs if " connected" in line), "")
            if not self.display_output:
                raise failure("The virtual display has no resizeable output.", "runtime_unavailable")
            manager = await spawn("openbox", env=self.env)
            self.resources.push_async_callback(terminate, manager)
            driver = await self.resources.enter_async_context(async_playwright())
            self.context = await driver.chromium.launch_persistent_context(
                str(self.profile),
                executable_path=browser.path,
                headless=False,
                env={**self.env},
                viewport=None,
                args=["--window-position=0,0"],
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
            for page in self.context.pages:
                await self.layout(page, self.state.viewport)
            await self.fit_display()
            if self.retired or self.page.is_closed():
                raise failure("Chromium closed during startup. Start the browser to try again.", "runtime_unavailable")
            self.state = self.state.model_copy(update={"status": "ready", "error": None})
            self.watch_process(display, "The virtual display")
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
                else "Browser startup failed. Check Playwright Chromium and Linux display dependencies."
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
            except Exception:
                self.end("VNC authentication could not be renewed. Start the browser to continue.")
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
            self.context = self.page = None
            self.control_socket = ""
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
    def __init__(self, *, root: Path, available: bool, authorize: Callable[[str], Awaitable[None]]) -> None:
        self.root, self.available, self.authorize = root, available, authorize
        self.sessions: dict[str, BrowserSession] = {}
        self.tickets: dict[str, tuple[str, str, int, bool, float]] = {}
        self.lifecycle = asyncio.Lock()
        self.installer = BrowserInstaller()

    def state(self, thread_id: str) -> BrowserState:
        session = self.sessions.get(thread_id)
        if session and session.page and session.page.is_closed():
            session.page_closed(session.page)
        return session.state if session else BrowserState()

    def binding(self, thread_id: str) -> str | None:
        session = self.sessions.get(thread_id)
        return session.state.session_id if session and not session.retired else None

    def session(self, thread_id: str, binding: str) -> BrowserSession:
        session = self.sessions.get(thread_id)
        if session is None or session.retired or session.state.session_id != binding:
            raise failure("Browser session changed. Refresh before acting.", "session_conflict")
        return session

    async def setup(self, thread_id: str) -> BrowserSetup:
        await self.authorize(thread_id)
        result = (
            self.installer.preparation
            if self.installer.running and self.installer.preparation is not None
            else await inspect_browser()
        )
        await self.authorize(thread_id)
        return result.model_copy(update={"installation": self.installer.state})

    async def install(self, thread_id: str) -> BrowserSetup:
        await self.authorize(thread_id)
        async with self.lifecycle:
            if self.installer.running:
                return await self.setup(thread_id)
            preparation = await self.setup(thread_id)
            if preparation.ready:
                return preparation
            if not preparation.installable:
                raise failure(preparation.install_message, "installation_unavailable")
            self.installer.start(preparation, lambda: self.authorize(thread_id))
            return preparation.model_copy(update={"installation": self.installer.state})

    async def enable(self, thread_id: str) -> BrowserState:
        await self.authorize(thread_id)
        async with self.lifecycle:
            if thread_id not in self.sessions:
                if sum(not session.retired for session in self.sessions.values()) >= 4:
                    raise failure("Stop an unused browser before enabling another.", "limit")
                await require_browser()
                await self.authorize(thread_id)
                self.sessions[thread_id] = BrowserSession(thread_id, self.root / thread_id)
            return self.state(thread_id)

    async def replace(self, session: BrowserSession) -> BrowserSession:
        if sum(not other.retired for other in self.sessions.values() if other is not session) >= 4:
            raise failure("Stop an unused browser before starting another.", "limit")
        await session.close()
        replacement = BrowserSession(session.thread_id, session.profile, viewport=session.state.viewport)
        replacement.requires_observation = True
        self.sessions[session.thread_id] = replacement
        return replacement

    async def start(self, thread_id: str, binding: str, *, explicit: bool = False) -> BrowserState:
        async with self.lifecycle:
            await self.authorize(thread_id)
            session = self.sessions.get(thread_id)
            if session is None or session.state.session_id != binding:
                raise failure("Browser session changed. Refresh before acting.", "session_conflict")
            if session.retired:
                if not explicit or session.state.status not in {"stopped", "failed"}:
                    raise failure("Browser session changed. Start the browser before acting.", "session_conflict")
                session = await self.replace(session)
            async with session.lock:
                await session.start()
            return session.state

    async def reset(self, thread_id: str, binding: str) -> BrowserState:
        async with self.lifecycle:
            await self.authorize(thread_id)
            session = await self.replace(self.session(thread_id, binding))
            async with session.lock:
                await session.start()
            return session.state

    async def resize(self, thread_id: str, request: BrowserViewportRequest) -> BrowserState:
        await self.authorize(thread_id)
        session = self.session(thread_id, request.session_id)
        async with session.lock:
            await self.authorize(thread_id)
            self.session(thread_id, request.session_id)
            if (
                session.state.control != "human"
                or not request.control_token
                or not secrets.compare_digest(request.control_token, session.control_token)
            ):
                raise failure("Take control before changing the browser viewport.", "control_required")
            if session.context is None:
                raise failure("Start the browser before changing its viewport.", "not_ready")
            if request.preset == session.state.viewport:
                return session.state
            try:
                await session.invalidate()
                session.requires_observation = True
                for page in session.context.pages:
                    if not page.is_closed():
                        await session.layout(page, request.preset)
                await session.fit_display()
                self.session(thread_id, request.session_id)
                session.state = session.state.model_copy(update={"viewport": request.preset})
            except Exception as exc:
                session.end("Browser resizing failed. Start the browser to continue.")
                raise failure("Browser resizing failed. Start the browser to continue.", "resize_failed") from exc
            return session.state

    async def stop(self, thread_id: str, binding: str) -> BrowserState:
        async with self.lifecycle:
            await self.authorize(thread_id)
            session = self.sessions.get(thread_id)
            if session is None or session.state.session_id != binding:
                raise failure("Browser session changed. Refresh before acting.", "session_conflict")
            session.end()
            await session.close()
            session.state = session.state.model_copy(
                update={"status": "stopped", "control": "agent", "url": "", "error": None}
            )
            return session.state

    async def control(self, thread_id: str, binding: str, *, human: bool) -> BrowserControl:
        session = self.session(thread_id, binding)
        # Mark the barrier before waiting for an in-flight operation; queued Agent calls fail.
        await self.authorize(thread_id)
        if human and session.state.control == "human":
            raise failure("The browser is already under human control.", "control_conflict")
        if human and session.page is None:
            raise failure("Start the browser before taking control.", "not_ready")
        session.state = session.state.model_copy(update={"control": "human"})
        session.released.clear()
        async with session.lock:
            await self.authorize(thread_id)
            self.session(thread_id, binding)
            await session.revoke()
            await session.invalidate()
            session.requires_observation = True
            if human:
                await session.cursor.hide()
                if session.page is None:
                    raise failure("Start the browser before taking control.", "not_ready")
                session.control_token = secrets.token_urlsafe(24)
            else:
                session.state = session.state.model_copy(update={"control": "agent"})
                session.released.set()
            return BrowserControl(state=session.state, control_token=session.control_token)

    async def access(self, thread_id: str, request: BrowserAccessRequest) -> BrowserAccess:
        await self.authorize(thread_id)
        session = self.session(thread_id, request.session_id)
        async with session.lock:
            await self.authorize(thread_id)
            self.session(thread_id, request.session_id)
            if session.page is None:
                raise failure("Start the browser before opening its desktop.", "not_ready")
            controlling = bool(request.control_token) and secrets.compare_digest(
                request.control_token, session.control_token
            )
            if request.control_token and (not controlling or session.state.control != "human"):
                raise failure("Human input authority has been revoked.", "control_conflict")
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
        reader, writer = await asyncio.open_unix_connection(session.control_socket)
        if session.generation != generation or session.retired:
            writer.close()
            raise failure("Viewer access was revoked.", "access_expired")
        if len(session.connections) >= 8:
            writer.close()
            raise failure("Too many browser viewers.", "limit")
        session.connections.add(writer)
        if controlling:
            # Recheck after opening the socket: two concurrent tickets cannot create two controllers.
            if session.controller_connection is not None:
                session.connections.discard(writer)
                writer.close()
                raise failure("A controller is already connected.", "control_conflict")
            session.controller_connection = writer
        return session, reader, writer

    async def disconnect(self, session: BrowserSession, writer: asyncio.StreamWriter) -> None:
        session.connections.discard(writer)
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
        await self.installer.close()
        # Shutdown owns cleanup even after Thread authority has been revoked.
        # Retire every binding first, and attempt all cleanups if one fails.
        try:
            async with AsyncExitStack() as resources:
                for session in self.sessions.values():
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
        # No page-operation lock or database transaction is retained while waiting.
        while session.state.control == "human":
            await session.released.wait()
            await self.browsers.authorize(self.thread_id)
            self.browsers.session(self.thread_id, self.binding)

    async def checked(self) -> BrowserSession:
        await self.browsers.authorize(self.thread_id)
        session = self.browsers.session(self.thread_id, self.binding)
        if session.state.control == "human":
            raise failure("The user controls the browser. Ask for handback before continuing.", "human_control")
        if session.context is not None:
            # Follow the actual foreground tab after a user opens a tab or login popup.
            # This collection occurs only after the takeover barrier has been released.
            for candidate in reversed(session.context.pages[-16:]):
                if candidate.is_closed():
                    continue
                try:
                    focused = await asyncio.wait_for(candidate.evaluate("document.hasFocus()"), 1)
                except Exception:
                    continue
                if focused:
                    if session.page != candidate:
                        await session.invalidate()
                        session.page = candidate
                        session.requires_observation = True
                    break
        if session.page is None or session.page.is_closed():
            raise failure("Browser is not running. Navigate to start it.", "not_ready")
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
            assert session.page is not None
            await session.invalidate()
            session.revision = secrets.token_hex(12)
            elements = []
            # Keep actual element handles, never infer a replacement target from a stale index.
            candidates = await session.page.locator(
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
                session.targets[target] = element
                elements.append(BrowserElement(target, description))
            for element in candidates[256:]:
                await element.dispose()
            text = (await session.page.locator("body").aria_snapshot())[:24_000]
            session.requires_observation = False
            session.state = session.state.model_copy(update={"url": session.page.url})
            return BrowserObservation(f"URL: {session.page.url}\n{text}", session.revision, tuple(elements))

    async def act(self, action: BrowserAction, *, target: str = "", value: str = "", revision: str = "") -> str:
        if action == "navigate":
            browser_url(value)
            await self.browsers.start(self.thread_id, self.binding)
        async with self.operation() as session:
            assert session.page is not None
            if session.requires_observation:
                raise failure(
                    "Observe the browser after handback or reopening before continuing.", "observation_required"
                )
            try:
                if action == "navigate":
                    await session.invalidate()
                    await session.page.goto(value, wait_until="domcontentloaded", timeout=20_000)
                elif action in {"click", "fill"}:
                    element = session.targets.get(target)
                    if (
                        not revision
                        or revision != session.revision
                        or element is None
                        or not await element.evaluate("e => e.isConnected")
                    ):
                        raise failure("Element reference expired. Observe again.", "reference_expired")
                    if action == "click":
                        await session.cursor.show(session.page, element)
                        await element.click()
                    else:
                        if await element.get_attribute("type") in {"password", "file"}:
                            raise failure("Use human takeover for password or file input.", "sensitive_input")
                        await session.cursor.show(session.page, element)
                        await element.fill(value)
                elif action == "press":
                    await session.cursor.show(session.page)
                    await session.page.keyboard.press(value)
                elif action == "wait":
                    await session.page.get_by_text(value, exact=False).first.wait_for(state="visible")
                session.state = session.state.model_copy(update={"url": session.page.url})
                return f"Browser {action} completed. Observe to verify the result."
            except HarnessUiError:
                raise
            except Exception as exc:
                raise failure("Browser action failed; its outcome may be uncertain. Observe before retrying.") from exc

    async def screenshot(self) -> bytes:
        async with self.operation() as session:
            assert session.page is not None
            picture = await session.page.screenshot(type="jpeg", quality=65)
            if len(picture) > 3 * 1024 * 1024:
                raise failure("Screenshot exceeds the browser output limit.")
            return picture
