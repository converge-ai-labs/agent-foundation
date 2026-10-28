"""Shared headed Chromium: cdp-use automation and platform-native viewing."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from a13n_harness.capabilities.browser import (
    BrowserError,
    BrowserHumanControl,
    BrowserObservation,
    BrowserQuery,
    BrowserScreenshot,
    BrowserTab,
    BrowserTabAction,
)
from a13n_harness.capabilities.browser.actions import Action, Clip, Condition
from a13n_harness.capabilities.browser.cdp import Chromium, Page
from a13n_harness.capabilities.browser.scope import CdpBrowserBackend
from pydantic import Field

from a13n_harness_ui.browser_capture import WindowPreview, window_ids
from a13n_harness_ui.browser_display import FIXED_DESKTOP, VncDesktop, sway_command, sway_windows
from a13n_harness_ui.browser_handback import BrowserHandback
from a13n_harness_ui.browser_macos import MacDesktop, open_capture_settings
from a13n_harness_ui.browser_setup import BrowserPlatform, BrowserSetup, host_platform, inspect_browser
from a13n_harness_ui.browser_vnc import VncConnection
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
    platform: BrowserPlatform = Field(default_factory=host_platform)
    session_id: str = ""
    status: Literal["disabled", "starting", "enabled", "ready", "stopped", "failed"] = "disabled"
    page_count: int = 0
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
    protocol: Literal["vnc", "jpeg"] = "vnc"
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


async def require_browser(configuration: BrowserConfiguration, *, no_sandbox: bool = False) -> BrowserSetup:
    setup = await inspect_browser(configuration, no_sandbox=no_sandbox)
    if not setup.ready or setup.browser is None:
        raise failure(setup.message, "runtime_not_ready")
    return setup


@dataclass
class Binding:
    identity: str = field(default_factory=lambda: "browser_" + secrets.token_hex(12))
    status: Literal["enabled", "ready", "stopped", "failed"] = "enabled"
    scope: CdpBrowserBackend | None = None
    preview_window: int | None = None
    viewport: BrowserPreset = "desktop"
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None

    @property
    def page(self) -> Page | None:
        if self.scope is None or not self.scope.targets:
            return None
        try:
            return self.scope.page
        except BrowserError:
            return None


class HostBrowsers:
    def __init__(
        self,
        *,
        root: Path,
        available: bool,
        authorize: Callable[[str], Awaitable[None]],
        configuration: BrowserConfiguration | None = None,
        no_sandbox: bool = False,
    ) -> None:
        self.configuration = configuration or BrowserConfiguration()
        self.no_sandbox = no_sandbox
        self.root = root / "profile"
        self.available, self.authorize = available, authorize
        self.handback = BrowserHandback()
        self.bindings: dict[str, Binding] = {}
        self.human_tabs: dict[str, tuple[str, str]] = {}
        self.runtime: Chromium | None = None
        self.runtime_failed = False
        self.prepared = False
        self.startup_error: str | None = None
        self.keepalive: str | None = None
        self.desktop: VncDesktop | MacDesktop | None = None
        self.native_windows: dict[int, int] = {}
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
        self.connections: dict[VncConnection | WindowPreview, str] = {}
        self.controller: VncConnection | None = None
        self.generation = 0

    @property
    def owners(self) -> dict[str, str]:
        return {
            target: thread
            for thread, item in self.bindings.items()
            if item.scope is not None
            for target in item.scope.targets
        }

    def state(self, thread_id: str) -> BrowserState:
        item = self.bindings.get(thread_id)
        if item is None:
            if not self.available or self.configuration.mode == "disabled":
                return BrowserState()
            return BrowserState(
                status="failed" if self.startup_error else "enabled" if self.runtime else "starting",
                error=self.startup_error,
            )
        return BrowserState(
            session_id=item.identity,
            status=item.status,
            page_count=len(item.scope.targets) if item.scope else 0,
            control="human" if self.human_thread else "agent",
            url=item.page.url if item.page else "",
            error=item.error,
            viewport=item.viewport,
            resize_available=bool(self.desktop and self.desktop.resize_available),
        )

    async def prepare(self, *, retry: bool = False) -> None:
        """Prepare the shared process without allocating conversation pages."""
        if not self.available or self.configuration.mode == "disabled":
            return
        async with self.control_lock, self.lifecycle:
            if self.prepared and not retry:
                return
            self.prepared = True
            self.startup_error = None
            try:
                await self.launch()
            except Exception as exc:
                self.failed(str(exc))

    async def admit(self, thread_id: str) -> str | None:
        """Capture an empty scope for an eligible Run; preparation failure is optional."""
        if not self.available or self.configuration.mode == "disabled":
            return None
        await self.authorize(thread_id)
        await self.prepare()
        if self.runtime is None or self.runtime_failed:
            return None
        item = self.bindings.get(thread_id)
        if item is None or item.status in {"stopped", "failed"}:
            item = self.bindings[thread_id] = Binding(status="ready")

            def check_access() -> None:
                self.checked(thread_id, item.identity)

            item.scope = CdpBrowserBackend(self.runtime, new_window=True, check_access=check_access)
        return item.identity

    async def open(self, thread_id: str) -> BrowserState:
        await self.authorize(thread_id)
        if self.configuration.mode == "disabled":
            raise failure("Browser is disabled in settings.", "disabled")
        await self.prepare(retry=True)
        binding = await self.admit(thread_id)
        if binding is None:
            raise failure(self.startup_error or "Hand back browser control before opening a page.", "not_ready")
        item = self.checked(thread_id, binding)
        if item.status == "ready" and item.page is None:
            await self.backend(thread_id, binding).tabs("new")
            return self.state(thread_id)
        return await self.start(thread_id, binding)

    def binding(self, thread_id: str) -> str | None:
        item = self.bindings.get(thread_id)
        return item.identity if item and item.status not in {"stopped", "failed"} else None

    def checked(self, thread_id: str, identity: str) -> Binding:
        item = self.bindings.get(thread_id)
        if item is None or item.identity != identity:
            raise failure("Browser session changed. Refresh before acting.", "session_conflict")
        return item

    async def inspect(self, configuration: BrowserConfiguration) -> BrowserSetup:
        return await inspect_browser(configuration, no_sandbox=self.no_sandbox)

    def require_macos(self) -> None:
        if not self.available:
            raise failure("Enable WebUI Host computer sharing first.", "disabled")
        if host_platform() != "macos":
            raise failure("Screen Recording settings require a macOS Host.", "unsupported")

    async def open_macos_settings(self) -> None:
        self.require_macos()
        try:
            await open_capture_settings()
        except (OSError, TimeoutError):
            raise failure("Could not open Screen Recording settings on this Mac.", "settings_unavailable") from None

    async def setup(self, thread_id: str) -> BrowserSetup:
        await self.authorize(thread_id)
        result = await self.inspect(self.configuration)
        await self.authorize(thread_id)
        return result

    async def enable(self, thread_id: str) -> BrowserState:
        await self.authorize(thread_id)
        await require_browser(self.configuration, no_sandbox=self.no_sandbox)
        self.bindings.setdefault(thread_id, Binding())
        return self.state(thread_id)

    def revoke_viewers(self, thread_id: str | None = None) -> None:
        for connection, owner in tuple(self.connections.items()):
            if thread_id is None or owner == thread_id:
                connection.close()
        self.tickets = {
            key: value for key, value in self.tickets.items() if thread_id is not None and value[0] != thread_id
        }

    async def settle_viewers(self) -> None:
        connections = tuple(self.connections)
        self.revoke_viewers()
        async with asyncio.timeout(5):
            await asyncio.gather(*(connection.wait_closed() for connection in connections))
        for connection in connections:
            self.connections.pop(connection, None)

    async def revoke(self) -> None:
        self.generation += 1
        self.control_token = ""
        self.controller = None
        try:
            await self.settle_viewers()
            if self.desktop:
                await self.desktop.settle_input()
        except Exception:
            self.failed("VNC input authority could not be renewed. Start the browser again.")
            raise

    def failed(self, reason: str) -> None:
        self.handback.stop()
        self.runtime_failed = True
        self.startup_error = reason
        self.generation += 1
        self.controller = None
        for item in self.bindings.values():
            item.closed.set()
            if item.scope:
                item.scope.retire()
            item.status, item.error = "failed", reason
        self.human_thread = self.control_token = ""
        self.released.set()
        self.revoke_viewers()

    async def launch(self) -> None:
        if (
            not self.runtime_failed
            and self.runtime is not None
            and self.process is not None
            and self.process.returncode is None
        ):
            return
        await self.close_runtime()
        preparation = await require_browser(self.configuration, no_sandbox=self.no_sandbox)
        assert preparation.browser is not None
        if preparation.platform == "macos":
            self.desktop = MacDesktop()
        else:
            assert preparation.vnc is not None
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
            *self.desktop.chromium_arguments,
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
        if preparation.platform == "linux":
            # The private desktop cannot unlock the Host's login keyring.
            # Basic storage keeps cookie initialization from waiting on it.
            arguments.append("--password-store=basic")
        if self.no_sandbox:
            arguments.append("--no-sandbox")
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
            self.runtime.opened = self.opened
            self.runtime.closed = self.page_closed
            initial = (await self.runtime.send("Target.getTargets"))["targetInfos"]
            keepalive = next(target for target in initial if target["type"] == "page")
            internal = await self.runtime.attach(keepalive["targetId"], keepalive.get("url", "about:blank"))
            self.keepalive = internal.target
            await self.runtime.evaluate(internal, "document.title='A13n Harness browser runtime'")
            if self.desktop.wayland:
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
            self.runtime_failed = False
        except BaseException:
            await self.close_runtime()
            raise

    def preview_windows(self) -> set[int] | None:
        if not isinstance(self.desktop, MacDesktop) or not self.process:
            return set()
        try:
            return window_ids(self.process.pid)
        except (ImportError, OSError):
            # An unknown baseline cannot establish a newly created window.
            return None

    async def bind_preview(self, item: Binding, before: set[int] | None) -> None:
        if not isinstance(self.desktop, MacDesktop) or before is None:
            return
        # A preview must belong to the exact newly created native window.
        for _ in range(40):
            after = self.preview_windows()
            if after is None:
                break
            candidates = after - before
            if candidates:
                if len(candidates) == 1:
                    item.preview_window = candidates.pop()
                break
            await asyncio.sleep(0.05)

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
        if self.desktop.wayland:
            await sway_command(self.desktop.env, await self.native_window(page), "focus")

    async def fit_desktop(self, width: int, height: int) -> None:
        assert self.runtime and self.desktop
        for target in self.owners:
            if candidate := self.runtime.pages.get(target):
                bounds = (await self.runtime.window(candidate))["bounds"]
                # Maximized/fullscreen windows follow the desktop; including
                # them would feed the screen size back into its own growth.
                if bounds.get("windowState", "normal") == "normal":
                    width, height = max(width, bounds["width"]), max(height, bounds["height"])
        # X11 constrains normal windows strictly below the screen dimensions.
        assert isinstance(self.desktop, VncDesktop)
        await self.desktop.resize(width + 1, height + 1)

    async def layout(self, page: Page, item: Binding, *, preset: BrowserPreset | None = None) -> None:
        assert self.runtime and self.desktop
        viewport = preset or item.viewport
        if self.desktop.resize_available:
            size = VIEWPORT_BY_PRESET[viewport]
            await self.runtime.resize_window(
                page, size.width, size.height, prepare_bounds=self.fit_desktop, allow_minimum=True
            )
        else:
            identity = (await self.runtime.window(page))["windowId"]
            await self.runtime.window_bounds(identity, {"windowState": "normal"})
            width, height = FIXED_DESKTOP
            if isinstance(self.desktop, MacDesktop):
                await self.runtime.window_bounds(identity, {"windowState": "maximized"})
            elif self.desktop.wayland:
                # Wayland leaves window placement to the compositor; Chromium's
                # CDP bounds do not move a native Wayland window.
                await sway_command(
                    self.desktop.env,
                    await self.native_window(page),
                    f"resize set {width} px {height} px, move position 0 px 0 px",
                )
            else:
                await self.runtime.window_bounds(identity, {"left": 0, "top": 0, "width": width, "height": height})
        if self.desktop.resize_available:
            await self.fit_desktop(32, 32)
        if viewport == "desktop" and self.desktop.window_manager:
            identity = (await self.runtime.window(page))["windowId"]
            await self.runtime.window_bounds(identity, {"windowState": "maximized"})

    def opened(self, info: dict) -> None:
        # Harness claims opener-linked popups. The Host records exact targets
        # created during human input and admits them only after revoking input.
        if info.get("type") == "page" and self.human_thread:
            item = self.bindings.get(self.human_thread)
            if item and item.status == "ready":
                self.human_tabs[info["targetId"]] = (self.human_thread, item.identity)

    def page_closed(self, target: str) -> None:
        if target == self.keepalive:
            self.failed("The browser runtime window closed. Start the browser to continue.")
            return
        self.human_tabs.pop(target, None)
        # Closing a tab changes membership, not the binding or input authority.
        # Empty scopes remain usable until explicit Stop/Reset or runtime failure.

    async def start(self, thread_id: str, binding: str, *, explicit: bool = False) -> BrowserState:
        await self.authorize(thread_id)
        existing = self.checked(thread_id, binding)
        if existing.status == "ready":
            return self.state(thread_id)
        async with self.control_lock, self.lifecycle:
            item = self.checked(thread_id, binding)
            if item.status in {"stopped", "failed"}:
                if not explicit:
                    raise failure("Browser stopped. Start it explicitly.", "session_conflict")
                item.closed.set()
                if self.runtime_failed:
                    await self.close_runtime()
                elif item.scope:
                    await item.scope.close()
                item = self.bindings[thread_id] = Binding(viewport=item.viewport)
            if self.human_thread:
                raise failure("Hand back browser control before opening a page.", "human_control")
            try:
                await self.launch()
                assert self.runtime is not None
                if item.page is None:
                    before = self.preview_windows()
                    page = await self.runtime.create(new_window=True)

                    def check_access() -> None:
                        self.checked(thread_id, item.identity)

                    if item.scope is None:
                        item.scope = CdpBrowserBackend(self.runtime, page, check_access=check_access)
                    else:
                        await item.scope.attach(page.target)
                    if self.desktop and not self.desktop.resize_available:
                        item.viewport = "desktop"
                    await self.layout(page, item)
                    await self.focus(page)
                    await self.bind_preview(item, before)
                item.status = "ready"
            except Exception as exc:
                item.status, item.error = "failed", str(exc)
                raise failure(f"Chromium startup failed: {exc}", "runtime_unavailable") from exc
            return self.state(thread_id)

    async def stop(self, thread_id: str, binding: str) -> BrowserState:
        await self.authorize(thread_id)
        async with self.control_lock, self.lifecycle:
            item = self.checked(thread_id, binding)
            item.closed.set()
            self.revoke_viewers(thread_id)
            if self.human_thread == thread_id:
                await self.release()
            item.status = "stopped"
            if item.scope:
                await item.scope.close()
            self.bindings[thread_id] = Binding(status="stopped", viewport=item.viewport)
            return self.state(thread_id)

    async def close_pages(self, thread_id: str, binding: str) -> BrowserState:
        """Drain and close this Thread's pages without revoking its Run binding."""
        await self.authorize(thread_id)
        async with self.control_lock, self.lifecycle:
            item = self.checked(thread_id, binding)
            self.revoke_viewers(thread_id)
            if self.human_thread == thread_id:
                await self.release()
            if item.scope:
                await item.scope.close()
            if self.runtime is not None and not self.runtime_failed:

                def check_access() -> None:
                    self.checked(thread_id, binding)

                item.scope = CdpBrowserBackend(self.runtime, new_window=True, check_access=check_access)
                item.preview_window = None
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
            if not self.desktop or not self.desktop.resize_available:
                raise failure("Desktop resizing is unavailable on this Host.", "resize_unavailable")
            if item.page is None:
                raise failure("Open a tab before resizing the browser.", "not_ready")
            assert self.runtime
            async with item.page.lock:
                self.require_control(thread_id, request.session_id, request.control_token)
                try:
                    await self.layout(item.page, item, preset=request.preset)
                    item.viewport = request.preset
                finally:
                    # A failed resize may still have moved native content.
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
        selected = focused or (visible[-1] if item.page not in visible and visible else None)
        if selected and item.scope:
            await item.scope.tabs("select", target=selected.target)

    async def release(self) -> None:
        previous = self.human_thread
        try:
            await self.revoke()
            for item in self.bindings.values():
                if item.status == "ready" and item.scope:
                    item.scope.resume()
            if self.runtime:
                live = {info["targetId"] for info in (await self.runtime.send("Target.getTargets"))["targetInfos"]}
                for target, (thread, identity) in tuple(self.human_tabs.items()):
                    item = self.bindings.get(thread)
                    if (
                        target in live
                        and target not in self.runtime.owners
                        and item
                        and item.identity == identity
                        and item.status == "ready"
                        and item.scope
                    ):
                        await item.scope.attach(target, select=False)
                self.human_tabs.clear()
            if previous:
                for thread, item in tuple(self.bindings.items()):
                    if item.status == "ready" and item.scope:
                        await self.sync_selected_tab(thread)
                        await self.release_keys(thread)
        except BaseException:
            self.failed("Browser handback failed. Start the browser again.")
            raise
        finally:
            self.human_thread = ""
            self.released.set()
        self.handback.released()

    async def control(self, thread_id: str, binding: str, *, human: bool) -> BrowserControl:
        await self.authorize(thread_id)
        async with self.control_lock:
            item = self.checked(thread_id, binding)
            if human:
                reconnecting = bool(self.human_thread)
                # Explicit transfer or recovery replaces the previous controller
                # without briefly releasing the automation barrier.
                if item.status != "ready":
                    raise failure("Start the browser before taking control.", "not_ready")
                if not reconnecting:
                    self.human_thread = thread_id
                self.released.clear()
                self.handback.takeover()
                # Existing per-target operations settle; newly admitted ones see
                # the barrier. Ordinary Agent work has no browser-wide queue.
                drain = asyncio.gather(
                    *(
                        candidate.scope.pause()
                        for candidate in self.bindings.values()
                        if candidate.status == "ready" and candidate.scope
                    ),
                    *tuple(self.operations),
                    return_exceptions=True,
                )
                try:
                    results = await asyncio.shield(drain)
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                    self.checked(thread_id, binding)
                    await self.revoke()
                    if item.page is not None:
                        await self.focus(item.page)
                    # Keep the previous Thread as the target owner until its
                    # native input is settled and the new window is focused.
                    self.human_thread = thread_id
                    self.control_token = secrets.token_urlsafe(24)
                    if self.runtime:
                        for page in self.runtime.pages.values():
                            page.invalidate()
                            page.requires_observation = True
                except BaseException:
                    try:
                        # Cancellation must not cancel completion futures or
                        # resume a scope whose pause is still draining.
                        results = await asyncio.shield(drain)
                        if any(isinstance(result, BaseException) for result in results):
                            self.failed("Browser takeover failed. Start the browser again.")
                        if not self.runtime_failed:
                            if reconnecting:
                                await self.revoke()
                            else:
                                await self.release()
                    except BaseException:
                        self.failed("Browser takeover cleanup failed. Start the browser again.")
                    raise
            else:
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
            if item.status != "ready" or self.desktop is None:
                raise failure("Start the browser before viewing.", "not_ready")
            controlling = (
                not isinstance(self.desktop, MacDesktop)
                and self.human_thread == thread_id
                and bool(request.control_token)
                and secrets.compare_digest(request.control_token, self.control_token)
            )
            if request.control_token and not controlling and not isinstance(self.desktop, MacDesktop):
                raise failure("Human input authority has been revoked.", "control_conflict")
            if item.page is not None and (not self.human_thread or controlling):
                await self.focus(item.page)
            now = time.monotonic()
            self.tickets = {key: value for key, value in self.tickets.items() if value[4] > now}
            if len(self.tickets) >= 128:
                raise failure("Too many pending viewers.", "limit")
            ticket = secrets.token_urlsafe(24)
            self.tickets[ticket] = (thread_id, request.session_id, self.generation, controlling, now + 30)
            return BrowserAccess(
                protocol="jpeg" if isinstance(self.desktop, MacDesktop) else "vnc",
                ticket=ticket,
                control=controlling,
                password=self.desktop.password if controlling else self.desktop.readonly_password,
            )

    async def connect(self, thread: str, ticket: str) -> VncConnection | WindowPreview:
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
        connection: VncConnection | WindowPreview
        if isinstance(self.desktop, MacDesktop):
            if controlling or item.preview_window is None or self.process is None:
                raise failure(
                    "Window preview unavailable. Reset the browser to bind a new window.", "preview_unavailable"
                )
            connection = await WindowPreview.start(self.process.pid, item.preview_window)
        else:
            connection = await self.desktop.connect(controlling=controlling)
        try:
            await self.authorize(thread)
            item = self.checked(thread, binding)
            if (
                item.closed.is_set()
                or item.status != "ready"
                or generation != self.generation
                or len(self.connections) >= 8
                or (controlling and self.controller is not None)
            ):
                raise failure("Viewer access was revoked.", "access_expired")
        except BaseException:
            connection.close()
            await connection.wait_closed()
            raise
        self.connections[connection] = thread
        if controlling and isinstance(connection, VncConnection):
            self.controller = connection
        return connection

    async def disconnect(self, connection: VncConnection | WindowPreview) -> None:
        connection.close()
        if self.controller is connection:
            async with self.control_lock:
                if self.controller is connection:
                    # Revoke native input without silently resuming Agent tools.
                    await self.revoke()
        self.connections.pop(connection, None)

    def backend(self, thread_id: str, binding: str) -> HostBrowserBackend:
        return HostBrowserBackend(self, thread_id, binding)

    async def close_runtime(self) -> None:
        self.generation += 1
        self.controller = None
        self.control_token = ""
        try:
            # Release native input before closing browser windows on the shared desktop.
            await self.settle_viewers()
        except (OSError, TimeoutError):
            pass
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
                    # Settle page-owned native UI before shutting down the process.
                    # Chromium can otherwise crash after password autofill and
                    # lose profile writes that have not reached disk yet.
                    for item in self.bindings.values():
                        if item.scope:
                            await item.scope.close()
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
                for item in self.bindings.values():
                    item.scope = None
        elif process is not None:
            from a13n_harness_ui.browser_display import terminate

            await terminate(process)
        self.connections.clear()
        if self.desktop:
            desktop, self.desktop = self.desktop, None
            await desktop.close()
        self.native_windows.clear()
        self.human_tabs.clear()

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
        if item.closed.is_set():
            raise failure("Browser session changed. Refresh before acting.", "session_conflict")

    @asynccontextmanager
    async def operation(self) -> AsyncIterator[CdpBrowserBackend]:
        await self.browsers.authorize(self.thread_id)
        item = self.browsers.checked(self.thread_id, self.binding)
        if self.browsers.human_thread:
            raise failure("The user controls the browser. Ask for handback.", "human_control")
        if item.status != "ready" or item.scope is None:
            raise failure("Browser is not running.", "not_ready")
        completion = asyncio.get_running_loop().create_future()
        self.browsers.operations.add(completion)
        try:
            yield item.scope
        finally:
            self.browsers.operations.discard(completion)
            completion.set_result(None)

    async def observe(self, query: BrowserQuery | None = None) -> BrowserObservation:
        async with self.operation() as scope:
            return await scope.observe(query)

    async def read(
        self,
        kind: Literal["text", "links", "tables", "forms"],
        *,
        target: str = "",
        revision: str = "",
        frame_index: int = 0,
        offset: int = 0,
        limit: int = 100,
    ) -> str:
        async with self.operation() as scope:
            return await scope.read(
                kind, target=target, revision=revision, frame_index=frame_index, offset=offset, limit=limit
            )

    async def screenshot(self, *, full_page: bool = False, clip: Clip | None = None) -> BrowserScreenshot:
        async with self.operation() as scope:
            return await scope.screenshot(full_page=full_page, clip=clip)

    async def navigate(
        self,
        url: str = "",
        *,
        action: Literal["goto", "back", "forward", "reload"] = "goto",
        timeout: float = 25,
    ) -> str:
        item = self.browsers.checked(self.thread_id, self.binding)
        if item.status == "ready" and item.page is None and action == "goto":
            await self.tabs("new")
            await self.observe()
        else:
            await self.browsers.start(self.thread_id, self.binding)
        async with self.operation() as scope:
            return await scope.navigate(url, action=action, timeout=timeout)

    async def interact(self, action: Action, *, revision: str = "") -> str:
        async with self.operation() as scope:
            return await scope.interact(action, revision=revision)

    async def wait(self, condition: Condition, *, revision: str = "", timeout: float = 10) -> str:
        async with self.operation() as scope:
            return await scope.wait(condition, revision=revision, timeout=timeout)

    async def tabs(self, action: BrowserTabAction, *, target: str = "", url: str = "") -> tuple[BrowserTab, ...]:
        async with self.operation() as scope:
            empty = action == "new" and not await scope.tabs("list")
            before = self.browsers.preview_windows() if empty else None
            tabs = await scope.tabs(action, target=target, url=url)
            page = scope.page if empty else None
        if page is not None:
            # Finish creation's admission before waiting for lifecycle locks:
            # Stop/takeover must still retire/drain an in-flight tab creation.
            async with self.browsers.control_lock, self.browsers.lifecycle:
                item = self.browsers.checked(self.thread_id, self.binding)
                if self.browsers.human_thread:
                    # Creation completed before takeover. Return that result
                    # without changing the human-controlled desktop.
                    return tabs
                async with self.operation() as scope:
                    item = self.browsers.checked(self.thread_id, self.binding)
                    if page.target not in scope.targets:
                        raise BrowserError("New tab closed before layout. List tabs before continuing.")
                    async with page.lock:
                        await self.browsers.layout(page, item)
                        await self.browsers.bind_preview(item, before)
        return tabs

    async def dialog(self, action: Literal["inspect", "accept", "dismiss"], *, text: str = "") -> str:
        async with self.operation() as scope:
            return await scope.dialog(action, text=text)

    async def upload(self, target: str, paths: tuple[str, ...], *, revision: str) -> str:
        async with self.operation() as scope:
            return await scope.upload(target, paths, revision=revision)

    async def downloads(self) -> str:
        async with self.operation() as scope:
            return await scope.downloads()
