"""Shared headed Chromium: cdp-use automation and private native VNC viewing."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from a13n_harness.capabilities.browser import (
    BrowserAction,
    BrowserError,
    BrowserHumanControl,
    BrowserObservation,
    BrowserTab,
    BrowserTabAction,
)
from a13n_harness.capabilities.browser.cdp import Chromium, Page
from pydantic import Field

from a13n_harness_ui.browser_cursor import Cursor
from a13n_harness_ui.browser_display import VncDesktop, sway_command, sway_windows
from a13n_harness_ui.browser_setup import BrowserSetup, inspect_browser
from a13n_harness_ui.configuration.models import BrowserConfiguration
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import SurfaceModel

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
    resize_available: bool = True


class BrowserCommand(SurfaceModel):
    session_id: str = Field(min_length=1, max_length=80)


class BrowserAccessRequest(BrowserCommand):
    control_token: str = Field(default="", max_length=80, repr=False)


class BrowserViewportRequest(BrowserCommand):
    control_token: str = Field(default="", max_length=80, repr=False)
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


async def require_browser(configuration: BrowserConfiguration) -> BrowserSetup:
    setup = await inspect_browser(configuration)
    if not setup.ready or setup.browser is None:
        raise failure(setup.message, "runtime_not_ready")
    return setup


@dataclass
class Binding:
    identity: str = field(default_factory=lambda: "browser_" + secrets.token_hex(12))
    status: Literal["enabled", "ready", "stopped", "failed"] = "enabled"
    page: Page | None = None
    viewport: BrowserPreset = "desktop"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None


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
        self.root = root / "profile"
        self.available, self.authorize = available, authorize
        self.bindings: dict[str, Binding] = {}
        self.owners: dict[str, str] = {}
        self.runtime: Chromium | None = None
        self.keepalive: str | None = None
        self.desktop: VncDesktop | None = None
        self.window_owners: dict[int, str] = {}
        self.native_windows: dict[int, int] = {}
        self.cursors: dict[str, Cursor] = {}
        self.process: asyncio.subprocess.Process | None = None
        self.monitor: asyncio.Task | None = None
        self.lifecycle = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.operations: set[asyncio.Future[None]] = set()
        self.released = asyncio.Event()
        self.released.set()
        self.human_thread = ""
        self.control_token = ""
        self.tickets: dict[str, tuple[str, str, int, bool, float]] = {}
        self.connections: dict[asyncio.StreamWriter, str] = {}
        self.controller: asyncio.StreamWriter | None = None
        self.generation = 0

    def state(self, thread_id: str) -> BrowserState:
        item = self.bindings.get(thread_id)
        if item is None:
            return BrowserState()
        return BrowserState(
            session_id=item.identity,
            status=item.status,
            control="human" if self.human_thread else "agent",
            url=item.page.url if item.page else "",
            error=item.error,
            viewport=item.viewport,
            resize_available=bool(self.desktop and self.desktop.desktop.xrandr),
        )

    def binding(self, thread_id: str) -> str | None:
        item = self.bindings.get(thread_id)
        return item.identity if item and item.status not in {"stopped", "failed"} else None

    def checked(self, thread_id: str, identity: str) -> Binding:
        item = self.bindings.get(thread_id)
        if item is None or item.identity != identity:
            raise failure("Browser session changed. Refresh before acting.", "session_conflict")
        return item

    async def setup(self, thread_id: str) -> BrowserSetup:
        await self.authorize(thread_id)
        result = await inspect_browser(self.configuration)
        await self.authorize(thread_id)
        return result

    async def enable(self, thread_id: str) -> BrowserState:
        await self.authorize(thread_id)
        await require_browser(self.configuration)
        self.bindings.setdefault(thread_id, Binding())
        return self.state(thread_id)

    def revoke_viewers(self, thread_id: str | None = None) -> None:
        for writer, owner in tuple(self.connections.items()):
            if thread_id is None or owner == thread_id:
                writer.close()
        self.tickets = {
            key: value for key, value in self.tickets.items() if thread_id is not None and value[0] != thread_id
        }

    async def revoke(self) -> None:
        self.generation += 1
        self.control_token = ""
        self.controller = None
        self.revoke_viewers()
        if self.desktop:
            try:
                await self.desktop.settle_input()
            except Exception:
                self.failed("VNC input authority could not be renewed. Start the browser again.")
                raise

    def failed(self, reason: str) -> None:
        self.generation += 1
        self.controller = None
        for item in self.bindings.values():
            item.closed.set()
            item.status, item.error = "failed", reason
        self.human_thread = self.control_token = ""
        self.released.set()
        self.revoke_viewers()

    async def launch(self) -> None:
        if self.runtime is not None and self.process is not None and self.process.returncode is None:
            return
        await self.close_runtime()
        preparation = await require_browser(self.configuration)
        assert preparation.browser is not None and preparation.vnc is not None
        self.desktop = VncDesktop(preparation.vnc)
        try:
            await self.desktop.start()
        except BaseException:
            await self.close_runtime()
            raise
        await asyncio.to_thread(self.root.mkdir, mode=0o700, parents=True, exist_ok=True)
        endpoint = self.root / "DevToolsActivePort"
        endpoint.unlink(missing_ok=True)
        arguments = [
            preparation.browser.path,
            f"--user-data-dir={self.root}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--ozone-platform={'wayland' if preparation.vnc.kind == 'wayvnc' else 'x11'}",
            "--window-position=0,0",
            "--mute-audio",
            # Owned windows must keep rendering for page-targeted CDP input
            # even when another Thread's native window is in front.
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        if preparation.vnc.kind == "wayvnc":
            # The private headless compositor uses Pixman and has no GPU/DRM
            # device. Render Chromium's Wayland surfaces in software as well.
            arguments.append("--disable-gpu")
        if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
            arguments.extend(["--no-sandbox", "--disable-dev-shm-usage"])
        self.process = await asyncio.create_subprocess_exec(
            *arguments, env=self.desktop.env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            async with asyncio.timeout(20):
                while True:
                    if self.process.returncode is not None:
                        raise RuntimeError(
                            "Chromium exited during startup; check executable, system libraries and profile ownership"
                        )
                    if endpoint.exists():
                        parts = (await asyncio.to_thread(endpoint.read_text)).splitlines()
                        if len(parts) == 2 and parts[0].isdecimal() and parts[1].startswith("/devtools/browser/"):
                            break
                    await asyncio.sleep(0.05)
            self.runtime = await Chromium.connect(f"ws://127.0.0.1:{parts[0]}{parts[1]}")
            downloads = self.root.parent / "downloads"
            await asyncio.to_thread(downloads.mkdir, mode=0o700, parents=True, exist_ok=True)
            await self.runtime.configure_downloads(str(downloads))
            self.runtime.download_started = lambda item: item.update(owner=self.owners.get(item["page"]))
            self.runtime.opened = self.opened
            self.runtime.closed = self.page_closed
            initial = (await self.runtime.send("Target.getTargets"))["targetInfos"]
            keepalive = next(target for target in initial if target["type"] == "page")
            internal = await self.runtime.attach(keepalive["targetId"], keepalive.get("url", "about:blank"))
            self.keepalive = internal.target
            await self.runtime.evaluate(internal, "document.title='A13n Harness browser runtime'")
            if self.desktop.desktop.kind == "wayvnc":
                await sway_command(self.desktop.env, await self.native_window(internal), "move scratchpad")
            else:
                identity = (await self.runtime.window(internal))["windowId"]
                await self.runtime.window_bounds(identity, {"windowState": "minimized"})
            process = self.process

            async def watch() -> None:
                assert self.desktop is not None
                waits = [
                    asyncio.create_task(component.wait())
                    for component in [process, *(component for component, _ in self.desktop.processes)]
                ]
                try:
                    await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                    if self.process is process:
                        self.failed("Browser or display exited. Start the browser to continue.")
                finally:
                    for task in waits:
                        task.cancel()
                    await asyncio.gather(*waits, return_exceptions=True)

            self.monitor = asyncio.create_task(watch())
        except BaseException:
            await self.close_runtime()
            raise

    async def native_window(self, page: Page) -> int:
        assert self.runtime and self.desktop
        identity = (await self.runtime.window(page))["windowId"]
        if identity not in self.native_windows:
            async with asyncio.timeout(5):
                while True:
                    windows = await sway_windows(self.desktop.env)
                    candidates = set(windows) - set(self.native_windows.values())
                    if len(candidates) > 1:
                        title = await self.runtime.evaluate(page, "document.title")
                        candidates = {
                            key for key in candidates if windows[key] == title or windows[key].startswith(title + " - ")
                        }
                    if len(candidates) == 1:
                        self.native_windows[identity] = candidates.pop()
                        break
                    await asyncio.sleep(0.025)
        return self.native_windows[identity]

    async def focus(self, page: Page) -> None:
        assert self.runtime and self.desktop
        await self.runtime.activate(page)
        if self.desktop.desktop.kind == "wayvnc":
            await sway_command(self.desktop.env, await self.native_window(page), "focus")

    async def layout(self, page: Page, item: Binding) -> None:
        assert self.runtime and self.desktop
        if self.desktop.desktop.xrandr:
            size = VIEWPORT_BY_PRESET[item.viewport]
            await self.runtime.resize_window(page, size.width, size.height)
        else:
            identity = (await self.runtime.window(page))["windowId"]
            await self.runtime.window_bounds(identity, {"windowState": "normal"})
            if self.desktop.desktop.kind == "wayvnc":
                # Wayland leaves window placement to the compositor; Chromium's
                # CDP bounds do not move a native Wayland window.
                await sway_command(
                    self.desktop.env,
                    await self.native_window(page),
                    "resize set 1280 px 900 px, move position 0 px 0 px",
                )
            else:
                await self.runtime.window_bounds(identity, {"left": 0, "top": 0, "width": 1280, "height": 900})
        width = height = 32
        for target in self.owners:
            if candidate := self.runtime.pages.get(target):
                bounds = (await self.runtime.window(candidate))["bounds"]
                width, height = max(width, bounds["width"]), max(height, bounds["height"])
        await self.desktop.resize(width, height)

    def opened(self, info: dict) -> None:
        if info.get("type") != "page" or self.runtime is None:
            return

        async def adopt() -> None:
            async with self.lifecycle:
                if not self.runtime or info["targetId"] in self.owners:
                    return
                page = await self.runtime.attach(info["targetId"], info.get("url", "about:blank"))
                identity = (await self.runtime.window(page))["windowId"]
                owner = (
                    self.owners.get(info.get("openerId", "")) or self.window_owners.get(identity) or self.human_thread
                )
                item = self.bindings.get(owner)
                if not item or item.status != "ready":
                    return
                self.owners[page.target] = owner
                self.window_owners[identity] = owner
                item.page = page
                page.requires_observation = True
                await self.layout(page, item)

        self.runtime.background(adopt())

    def page_closed(self, target: str) -> None:
        if target == self.keepalive:
            self.failed("The browser runtime window closed. Start the browser to continue.")
            return
        self.cursors.pop(target, None)
        owner = self.owners.pop(target, None)
        if self.runtime is not None:
            self.runtime.pages.pop(target, None)
        if owner and (item := self.bindings.get(owner)) and item.page and item.page.target == target:
            others = (
                [page for key, page in self.runtime.pages.items() if self.owners.get(key) == owner]
                if self.runtime
                else []
            )
            if others:
                item.page = others[-1]
                item.page.invalidate()
                item.page.requires_observation = True
                return
            item.closed.set()
            self.bindings[owner] = Binding(status="stopped", viewport=item.viewport)
            self.revoke_viewers(owner)
            if self.human_thread == owner:
                self.control_token = ""

                async def settle() -> None:
                    async with self.control_lock:
                        if self.human_thread == owner:
                            await self.release()

                if self.runtime:
                    self.runtime.background(settle())

    async def start(self, thread_id: str, binding: str, *, explicit: bool = False) -> BrowserState:
        await self.authorize(thread_id)
        existing = self.checked(thread_id, binding)
        if existing.status == "ready" and existing.page is not None:
            return self.state(thread_id)
        async with self.control_lock, self.lifecycle:
            item = self.checked(thread_id, binding)
            if item.status in {"stopped", "failed"}:
                if not explicit:
                    raise failure("Browser stopped. Start it explicitly.", "session_conflict")
                item.closed.set()
                item = self.bindings[thread_id] = Binding(viewport=item.viewport)
            if self.human_thread and self.human_thread != thread_id:
                raise failure("Hand back browser control before opening another page.", "human_control")
            try:
                await self.launch()
                assert self.runtime is not None
                if item.page is None:
                    page = await self.runtime.create(new_window=True)
                    self.owners[page.target] = thread_id
                    self.window_owners[(await self.runtime.window(page))["windowId"]] = thread_id
                    item.page = page
                    if self.desktop and self.desktop.desktop.xrandr is None:
                        item.viewport = "desktop"
                    await self.layout(page, item)
                    await self.focus(page)
                item.status = "ready"
            except Exception as exc:
                item.status, item.error = "failed", str(exc)
                raise failure(
                    "Chromium startup failed. Check the executable and shared profile.", "runtime_unavailable"
                ) from exc
            return self.state(thread_id)

    async def stop(self, thread_id: str, binding: str) -> BrowserState:
        await self.authorize(thread_id)
        async with self.control_lock, self.lifecycle:
            item = self.checked(thread_id, binding)
            item.closed.set()
            self.revoke_viewers(thread_id)
            if self.human_thread == thread_id:
                await self.release()
            self.bindings[thread_id] = Binding(status="stopped", viewport=item.viewport)
            if self.runtime:
                for target, owner in tuple(self.owners.items()):
                    if owner == thread_id:
                        self.owners.pop(target, None)
                        page = self.runtime.pages.get(target)
                        if page:
                            async with page.lock:
                                await self.runtime.close_page(page)
            self.window_owners = {key: owner for key, owner in self.window_owners.items() if owner != thread_id}
            return self.state(thread_id)

    async def reset(self, thread_id: str, binding: str) -> BrowserState:
        if self.human_thread and self.human_thread != thread_id:
            raise failure("Hand back control before resetting another page.", "human_control")
        stopped = await self.stop(thread_id, binding)
        return await self.start(thread_id, stopped.session_id, explicit=True)

    async def resize(self, thread_id: str, request: BrowserViewportRequest) -> BrowserState:
        await self.authorize(thread_id)
        async with self.control_lock, self.lifecycle:
            item = self.require_control(thread_id, request.session_id, request.control_token)
            if not self.desktop or not self.desktop.desktop.xrandr:
                raise failure("Desktop resizing is unavailable on this Host.", "resize_unavailable")
            assert self.runtime and item.page
            async with item.page.lock:
                self.require_control(thread_id, request.session_id, request.control_token)
                item.viewport = request.preset
                await self.layout(item.page, item)
                item.page.invalidate()
                item.page.requires_observation = True
        return self.state(thread_id)

    async def release_keys(self, thread: str) -> None:
        item = self.bindings.get(thread)
        if self.runtime and item and item.page:
            async with item.page.lock:
                await self.runtime.release_keys(item.page)

    async def sync_selected_tab(self, thread_id: str) -> None:
        item = self.bindings.get(thread_id)
        if not item or not self.runtime:
            return
        runtime = self.runtime
        pages = [page for key, page in runtime.pages.items() if self.owners.get(key) == thread_id]

        async def visibility(page: Page):
            if page.dialog:
                return page, {"visible": True, "focused": True}
            try:
                async with asyncio.timeout(1):
                    state = await runtime.evaluate(
                        page, "({visible:document.visibilityState==='visible',focused:document.hasFocus()})"
                    )
                    return page, state if isinstance(state, dict) else {}
            except (BrowserError, TimeoutError):
                return page, {}

        states = await asyncio.gather(*(visibility(page) for page in pages))
        focused = next((page for page, state in states if state.get("focused")), None)
        visible = [page for page, state in states if state.get("visible")]
        if focused:
            item.page = focused
        elif item.page not in visible and visible:
            item.page = visible[-1]

    async def release(self) -> None:
        previous = self.human_thread
        try:
            await self.revoke()
            if previous:
                await self.sync_selected_tab(previous)
                await self.release_keys(previous)
        finally:
            self.human_thread = ""
            if self.runtime:
                for page in self.runtime.pages.values():
                    page.invalidate()
                    page.requires_observation = True
            self.released.set()

    async def control(self, thread_id: str, binding: str, *, human: bool) -> BrowserControl:
        await self.authorize(thread_id)
        async with self.control_lock:
            item = self.checked(thread_id, binding)
            if human:
                if self.human_thread:
                    raise failure("Browser is already under human control.", "control_conflict")
                if item.status != "ready":
                    raise failure("Start the browser before taking control.", "not_ready")
                self.human_thread = thread_id
                self.released.clear()
                # Existing per-target operations settle; newly admitted ones see
                # the barrier. Ordinary Agent work has no browser-wide queue.
                await asyncio.gather(*tuple(self.operations), return_exceptions=True)
                self.checked(thread_id, binding)
                await self.revoke()
                await asyncio.gather(*(cursor.hide() for cursor in self.cursors.values()))
                assert item.page is not None
                await self.focus(item.page)
                self.control_token = secrets.token_urlsafe(24)
                if self.runtime:
                    for page in self.runtime.pages.values():
                        page.invalidate()
                        page.requires_observation = True
            else:
                if self.human_thread and self.human_thread != thread_id:
                    raise failure("Hand back control in its owning Thread.", "control_conflict")
                await self.release()
            return BrowserControl(state=self.state(thread_id), control_token=self.control_token)

    def require_control(self, thread: str, binding: str, token: str) -> Binding:
        item = self.checked(thread, binding)
        if not token or self.human_thread != thread or not secrets.compare_digest(token, self.control_token):
            raise failure("Take control before operating this page.", "control_required")
        return item

    async def access(self, thread_id: str, request: BrowserAccessRequest) -> BrowserAccess:
        await self.authorize(thread_id)
        async with self.control_lock:
            item = self.checked(thread_id, request.session_id)
            if item.status != "ready" or item.page is None or self.desktop is None:
                raise failure("Start the browser before viewing.", "not_ready")
            controlling = (
                self.human_thread == thread_id
                and bool(request.control_token)
                and secrets.compare_digest(request.control_token, self.control_token)
            )
            if request.control_token and not controlling:
                raise failure("Human input authority has been revoked.", "control_conflict")
            if not self.human_thread or controlling:
                await self.focus(item.page)
            now = time.monotonic()
            self.tickets = {key: value for key, value in self.tickets.items() if value[4] > now}
            if len(self.tickets) >= 128:
                raise failure("Too many pending viewers.", "limit")
            ticket = secrets.token_urlsafe(24)
            self.tickets[ticket] = (thread_id, request.session_id, self.generation, controlling, now + 30)
            return BrowserAccess(
                ticket=ticket,
                control=controlling,
                password=self.desktop.password if controlling else self.desktop.readonly_password,
            )

    async def connect(self, thread: str, ticket: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        claim = self.tickets.pop(ticket, None)
        if claim is None or claim[0] != thread or claim[4] <= time.monotonic():
            raise failure("Viewer access expired.", "access_expired")
        _, binding, generation, controlling, _ = claim
        await self.authorize(thread)
        item = self.checked(thread, binding)
        if generation != self.generation or not self.desktop or item.status != "ready":
            raise failure("Viewer access was revoked.", "access_expired")
        if len(self.connections) >= 8 or (controlling and self.controller is not None):
            raise failure("Too many viewers or another controller is connected.", "limit")
        path = (
            self.desktop.readonly_socket
            if not controlling and self.desktop.readonly_socket
            else self.desktop.control_socket
        )
        reader, writer = await asyncio.open_unix_connection(path)
        try:
            await self.authorize(thread)
            self.checked(thread, binding)
            if (
                generation != self.generation
                or len(self.connections) >= 8
                or (controlling and self.controller is not None)
            ):
                raise failure("Viewer access was revoked.", "access_expired")
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise
        self.connections[writer] = thread
        if controlling:
            self.controller = writer
        return reader, writer

    async def disconnect(self, writer: asyncio.StreamWriter) -> None:
        self.connections.pop(writer, None)
        writer.close()
        if self.controller is writer:
            async with self.control_lock:
                if self.controller is writer:
                    # Revoke native input without silently resuming Agent tools.
                    await self.revoke()

    def backend(self, thread_id: str, binding: str) -> HostBrowserBackend:
        return HostBrowserBackend(self, thread_id, binding)

    async def close_runtime(self) -> None:
        monitor, self.monitor = self.monitor, None
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        runtime, self.runtime = self.runtime, None
        self.keepalive = None
        process, self.process = self.process, None
        if runtime:
            try:
                if process is not None and process.returncode is None:
                    await runtime.close_browser()
            except BrowserError:
                pass
            finally:
                if process is not None and process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(), 8)
                    except TimeoutError:
                        from a13n_harness_ui.browser_display import terminate

                        await terminate(process)
                await runtime.close()
        elif process is not None:
            from a13n_harness_ui.browser_display import terminate

            await terminate(process)
        self.revoke_viewers()
        self.connections.clear()
        self.controller = None
        if self.desktop:
            desktop, self.desktop = self.desktop, None
            await desktop.close()
        self.window_owners.clear()
        self.native_windows.clear()
        self.cursors.clear()
        self.owners.clear()

    async def close(self) -> None:
        self.failed("Browser stopped.")
        await self.close_runtime()
        self.bindings.clear()


class HostBrowserBackend:
    def __init__(self, browsers: HostBrowsers, thread_id: str, binding: str) -> None:
        self.browsers, self.thread_id, self.binding = browsers, thread_id, binding

    async def wait_for_handback(self) -> None:
        item = self.browsers.checked(self.thread_id, self.binding)
        waits = [asyncio.create_task(self.browsers.released.wait()), asyncio.create_task(item.closed.wait())]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in waits:
                task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)
        self.browsers.checked(self.thread_id, self.binding)

    @asynccontextmanager
    async def operation(self, *, allow_closed: bool = False) -> AsyncIterator[tuple[Chromium, Binding, Page]]:
        await self.browsers.authorize(self.thread_id)
        item = self.browsers.checked(self.thread_id, self.binding)
        if self.browsers.human_thread:
            raise failure("The user controls the browser. Ask for handback.", "human_control")
        if item.status != "ready" or item.page is None or self.browsers.runtime is None:
            raise failure("Browser is not running.", "not_ready")
        completion = asyncio.get_running_loop().create_future()
        self.browsers.operations.add(completion)
        try:
            async with item.lock:
                self.browsers.checked(self.thread_id, self.binding)
                page = item.page
                assert page is not None
                async with page.lock:
                    self.browsers.checked(self.thread_id, self.binding)
                    if self.browsers.human_thread:
                        raise failure("The user controls the browser. Ask for handback.", "human_control")
                    if self.browsers.runtime is None:
                        raise failure("Browser is not running.", "not_ready")
                    yield self.browsers.runtime, item, page
                    if not allow_closed or self.browsers.state(self.thread_id).status != "stopped":
                        self.browsers.checked(self.thread_id, self.binding)
        finally:
            self.browsers.operations.discard(completion)
            completion.set_result(None)

    async def observe(self) -> BrowserObservation:
        async with self.operation() as (runtime, _, page):
            return await runtime.observe(page)

    async def screenshot(self) -> bytes:
        async with self.operation() as (runtime, _, page):
            return await runtime.screenshot(page)

    async def act(self, action: BrowserAction, *, target: str = "", value: str = "", revision: str = "") -> str:
        if action == "navigate":
            await self.browsers.start(self.thread_id, self.binding)
        async with self.operation() as (runtime, _, page):
            if action in {"click", "fill", "press"}:
                cursor = self.browsers.cursors.setdefault(page.target, Cursor())
                reference = page.elements.get(target) if revision == page.revision else None
                backend_id = reference.backend if reference and reference.session == page.session else None
                await cursor.show(runtime, page, backend_id)
            return await runtime.act(page, action, target=target, value=value, revision=revision)

    async def tabs(self, action: BrowserTabAction, *, target: str = "", url: str = "") -> tuple[BrowserTab, ...]:
        async with self.operation(allow_closed=action == "close") as (runtime, item, page):
            if action in {"select", "close"} and (
                self.browsers.owners.get(target) != self.thread_id or target not in runtime.pages
            ):
                raise failure("Tab does not belong to this conversation. List tabs again.", "tab_forbidden")
            if action == "new":
                created = await runtime.new_tab(page)
                self.browsers.owners[created.target] = self.thread_id
                self.browsers.window_owners[(await runtime.window(created))["windowId"]] = self.thread_id
                page.invalidate()
                item.page = created
                created.requires_observation = True
                if url:
                    await runtime.navigate(created, url)
            elif action == "select":
                page.invalidate()
                item.page = runtime.pages[target]
                item.page.invalidate()
                item.page.requires_observation = True
                await runtime.activate(item.page)
            elif action == "close":
                closing = runtime.pages[target]
                await runtime.close_page(closing)
                self.browsers.page_closed(target)
            pages = [page for key, page in runtime.pages.items() if self.browsers.owners.get(key) == self.thread_id]
            return await runtime.tab_list(pages, self.browsers.bindings[self.thread_id].page) if pages else ()

    async def scroll(self, *, x: int, y: int, target: str = "", revision: str = "") -> str:
        async with self.operation() as (runtime, _, page):
            return await runtime.scroll(page, x=x, y=y, target=target, revision=revision)

    async def dialog(self, action: Literal["inspect", "accept", "dismiss"], *, text: str = "") -> str:
        async with self.operation() as (runtime, _, page):
            return await runtime.dialog(page, action, text=text)

    async def upload(self, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        async with self.operation() as (runtime, _, page):
            if not 1 <= len(paths) <= 16:
                raise failure("Select 1-16 regular files on the browser Host.", "upload_invalid")

            def valid_files():
                return all(Path(path).is_absolute() and Path(path).is_file() for path in paths)

            if not await asyncio.to_thread(valid_files):
                raise failure(
                    "Upload paths must be existing absolute regular files on the browser Host.", "upload_invalid"
                )
            return await runtime.upload(page, target, paths, revision=revision)

    async def downloads(self) -> str:
        async with self.operation() as (runtime, _, _):
            targets = {key for key, owner in self.browsers.owners.items() if owner == self.thread_id}
            targets.update(
                item["page"] for item in runtime.download_records.values() if item.get("owner") == self.thread_id
            )
            return runtime.downloads(targets)
