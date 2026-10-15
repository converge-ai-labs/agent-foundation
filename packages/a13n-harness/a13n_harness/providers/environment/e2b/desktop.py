"""Bounded async adapter for the official synchronous E2B Desktop SDK."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from functools import cached_property, wraps
from typing import TYPE_CHECKING

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.computer import (
    ComputerClick,
    ComputerDrag,
    ComputerInput,
    ComputerMove,
    ComputerPressKeys,
    ComputerScroll,
    ComputerTypeText,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_harness.providers.environment.e2b.errors import sdk_errors

if TYPE_CHECKING:
    from e2b import AsyncSandbox
    from e2b.connection_config import ApiParams
    from e2b_desktop import Sandbox


class DesktopDescription(BaseModel):
    model_config = ConfigDict(frozen=True)
    width: int = Field(ge=256, le=4096)
    height: int = Field(ge=256, le=4096)
    generation: str


async def run_sync[T](operation: Callable[[], T]) -> T:
    # A cancelled waiter must not release the input lock or close the client while
    # its SDK thread is still dispatching input. Drain it, without replaying it.
    pending = asyncio.create_task(to_thread.run_sync(operation))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not pending.cancelled():
            pending.exception()
        raise


def configure(client: Sandbox, configuration: E2BEnvironmentConfiguration) -> None:
    """Keep SDK-generated commands intact while supplying execution policy."""
    from e2b import CommandResult

    def bounded[**P, R](run: Callable[P, R]) -> Callable[P, R]:
        @wraps(run)
        def call(*args: P.args, **kwargs: P.kwargs) -> R:
            kwargs.setdefault("user", configuration.user)
            kwargs.setdefault("envs", {"DISPLAY": configuration.desktop_display, "LC_ALL": "C.UTF-8"})
            kwargs.setdefault("timeout", configuration.request_timeout_seconds)
            kwargs.setdefault("request_timeout", configuration.request_timeout_seconds)
            result = run(*args, **kwargs)
            # xdotool may fail Unicode input with exit 0 and an error on stderr.
            if isinstance(result, CommandResult) and result.stderr.strip():
                raise EnvironmentError("Desktop command returned diagnostic errors.", code="environment_unavailable")
            return result

        return call

    client.commands.run = bounded(client.commands.run)


async def create_desktop(
    configuration: E2BEnvironmentConfiguration,
    *,
    timeout: int,
    metadata: dict[str, str],
    options: ApiParams,
    allocated: Callable[[str], None],
) -> None:
    from typing import Unpack

    from e2b.sandbox.main import SandboxOpts
    from e2b_desktop import Sandbox

    def create() -> None:
        clients: list[Sandbox] = []

        class ManagedDesktop(Sandbox):
            def __init__(self, **opts: Unpack[SandboxOpts]) -> None:
                super().__init__(**opts)
                clients.append(self)
                allocated(self.sandbox_id)
                configure(self, configuration)

        try:
            ManagedDesktop.create(
                template=configuration.template,
                resolution=(configuration.desktop_width, configuration.desktop_height),
                dpi=configuration.desktop_dpi,
                display=configuration.desktop_display,
                timeout=timeout,
                metadata=metadata,
                envs={"LC_ALL": "C.UTF-8"},
                allow_internet_access=configuration.allow_internet_access,
                lifecycle={"on_timeout": "kill", "auto_resume": False},
                **options,
            )
        finally:
            for client in clients:
                client._envd_api.close()

    with sdk_errors(mutation=True):
        await run_sync(create)


class Desktop:
    def __init__(self, sandbox: AsyncSandbox, configuration: E2BEnvironmentConfiguration) -> None:
        self.sandbox = sandbox
        self.configuration = configuration

    @cached_property
    def client(self) -> Sandbox:
        from e2b_desktop import Sandbox

        # Read-only attachment: connect() can resume/renew a sandbox. Reuse the
        # already validated base client's token, domain and routing instead.
        client = Sandbox(
            sandbox_id=self.sandbox.sandbox_id,
            sandbox_domain=self.sandbox.sandbox_domain,
            envd_version=self.sandbox._envd_version,
            envd_access_token=self.sandbox._envd_access_token,
            traffic_access_token=self.sandbox.traffic_access_token,
            connection_config=self.sandbox.connection_config,
        )
        configure(client, self.configuration)
        return client

    def _describe(self) -> DesktopDescription:
        width, height = self.client.get_screen_size()
        # SDK geometry alone cannot fence observations across an X server restart.
        identity = self.client.commands.run("ps -C Xvfb -o pid=,lstart= && pgrep -x xfce4-session").stdout.strip()
        if not identity:
            raise EnvironmentError("E2B desktop is unavailable.", code="environment_unavailable")
        return DesktopDescription(width=width, height=height, generation=hashlib.sha256(identity.encode()).hexdigest())

    async def describe(self) -> DesktopDescription:
        with sdk_errors():
            return await run_sync(self._describe)

    async def observe(self) -> tuple[DesktopDescription, bytes]:
        def capture() -> tuple[DesktopDescription, bytes]:
            before = self._describe()
            data = bytes(self.client.screenshot())
            if self._describe() != before:
                raise EnvironmentError("E2B desktop changed during capture.", code="environment_stale_mount")
            if len(data) > 32 * 1024 * 1024:
                raise EnvironmentError("E2B screenshot is too large.", code="environment_too_large")
            return before, data

        with sdk_errors():
            return await run_sync(capture)

    def _move(self, x: int, y: int) -> None:
        # SDK 2.7 uses mousemove --sync, which can hang at the current position.
        if self.client.get_cursor_position() != (x, y):
            self.client.move_mouse(x, y)

    def _write(self, text: str) -> None:
        # Reusing xdotool's temporary Unicode keycode within one command can
        # drop/repeat glyphs in GTK. Isolate non-ASCII characters into SDK calls
        # at its default pace. LF needs a native Return key in those apps too.
        deadline = time.monotonic() + self.configuration.request_timeout_seconds

        def check_deadline() -> None:
            if time.monotonic() >= deadline:
                raise TimeoutError("E2B desktop text input exceeded its time budget.")

        for index, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
            if index:
                check_deadline()
                self.client.press("enter")
            for chunk in re.finditer(r"[\x00-\x7f]{1,25}|[^\x00-\x7f]", line):
                check_deadline()
                self.client.write(chunk.group())

    async def execute(self, request: ComputerInput, basis: DesktopDescription | None) -> None:
        def dispatch() -> None:
            if basis is not None and self._describe() != basis:
                raise EnvironmentError(
                    "E2B screenshot reference is stale.",
                    code="environment_stale_mount",
                    details={"dispatch_stage": "pre_dispatch"},
                )
            if isinstance(request, (ComputerClick, ComputerMove, ComputerScroll)):
                self._move(request.point.x, request.point.y)
            if isinstance(request, ComputerClick):
                click = {
                    "left": self.client.left_click,
                    "right": self.client.right_click,
                    "middle": self.client.middle_click,
                }[request.button]
                # Move separately: SDK click(x, y) ignores zero coordinates.
                if request.button == "left" and request.count == 2:
                    self.client.double_click()
                else:
                    for _ in range(request.count):
                        click()
            elif isinstance(request, ComputerDrag):
                self._move(request.start.x, request.start.y)
                try:
                    self.client.mouse_press(request.button)
                    started = time.monotonic()
                    steps = 10
                    for step in range(1, steps + 1):
                        time.sleep(max(0, started + request.duration_ms / 1000 * step / steps - time.monotonic()))
                        self._move(
                            request.start.x + (request.end.x - request.start.x) * step // steps,
                            request.start.y + (request.end.y - request.start.y) * step // steps,
                        )
                finally:
                    self.client.mouse_release(request.button)
            elif isinstance(request, ComputerScroll):
                if request.delta_y:
                    self.client.scroll("down" if request.delta_y > 0 else "up", abs(request.delta_y))
            elif isinstance(request, ComputerTypeText):
                self._write(request.text)
            elif isinstance(request, ComputerPressKeys):
                self.client.press(list(request.keys))

        def execute() -> None:
            try:
                dispatch()
            except BaseException as error:
                if isinstance(error, EnvironmentError) and error.details.get("dispatch_stage") == "pre_dispatch":
                    raise
                # The SDK has no key-release method. Only this failure cleanup
                # uses its command channel; normal gestures remain SDK calls.
                try:
                    if isinstance(request, ComputerPressKeys):
                        from e2b_desktop.main import map_key

                        self.client.commands.run("xdotool keyup " + " ".join(map_key(key) for key in request.keys))
                    elif isinstance(request, ComputerClick):
                        self.client.mouse_release(request.button)
                except Exception:
                    pass
                raise

        with sdk_errors(mutation=True):
            await run_sync(execute)

    async def close(self) -> None:
        if "client" in self.__dict__:
            await run_sync(self.client._envd_api.close)
