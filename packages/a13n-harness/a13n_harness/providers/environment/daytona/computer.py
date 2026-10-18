"""Daytona's official Computer Use HTTP API behind the shared Computer facet."""

import asyncio
import base64
import binascii
import hashlib
import io
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from anyio import to_thread
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.computer import (
    ComputerActionResult,
    ComputerClick,
    ComputerDescription,
    ComputerDrag,
    ComputerInput,
    ComputerMove,
    ComputerObservation,
    ComputerPoint,
    ComputerPressKeys,
    ComputerScreenshot,
    ComputerScroll,
    ComputerTarget,
    ComputerTypeText,
)
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import (
    COMPUTER_ACTIONS,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationReceipt,
    EnvironmentPermissionSet,
)
from a13n_harness.providers.environment.native.commands import NativeCommands
from a13n_harness.providers.environment.native.http import NativeHTTP, decode_response


class Display(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: int
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    x: int
    y: int
    active: bool = Field(alias="isActive")


class Displays(BaseModel):
    displays: list[Display]


class Screenshot(BaseModel):
    screenshot: str = Field(max_length=32 * 1024 * 1024)


@dataclass(frozen=True)
class Geometry:
    display: Display
    incarnation: str


@dataclass(frozen=True)
class Observation:
    value: ComputerObservation
    geometry: Geometry
    expires: float


def with_computer(value: EnvironmentDescriptor) -> EnvironmentDescriptor:
    actions = value.permissions.operations | COMPUTER_ACTIONS
    return value.model_copy(
        update={
            "operation_families": value.operation_families | {"computer"},
            "permissions": EnvironmentPermissionSet(operations=actions),
        }
    )


def encode_image(data: bytes, geometry: Geometry, maximum: int) -> tuple[bytes, int, int]:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.size != (geometry.display.width, geometry.display.height):
                raise EnvironmentError("Desktop geometry changed during capture.", code="environment_stale_mount")
            image = source.convert("RGB")
            image.thumbnail((maximum, maximum))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
            encoded = output.getvalue()
            if len(encoded) > 4 * 1024 * 1024:
                raise EnvironmentError("Desktop screenshot exceeds its limit.", code="environment_too_large")
            return encoded, image.width, image.height
    except (OSError, ValueError, Image.DecompressionBombError):
        raise EnvironmentError("Invalid desktop screenshot.", code="environment_provider_failure") from None


class DaytonaComputer:
    def __init__(self, commands: NativeCommands, toolbox: NativeHTTP, sandbox_id: str):
        self.commands = commands
        self.toolbox = toolbox
        self.path = sandbox_id + "/computeruse/"
        self.lock = asyncio.Lock()
        self.blocked = False
        self.observations: OrderedDict[str, Observation] = OrderedDict()

    def check_open(self) -> None:
        if self.commands.closed:
            raise EnvironmentError("Daytona execution is closed.", code="environment_closed")

    async def geometry(self) -> Geometry:
        self.check_open()
        result = decode_response(Displays, await self.toolbox.request("GET", self.path + "display/info"), "daytona")
        active = [d for d in result.displays if d.active]
        if len(active) != 1 or active[0].x != 0 or active[0].y != 0:
            raise EnvironmentError("Daytona requires one desktop display at origin.", code="environment_unsupported")
        # Geometry alone cannot reject observations after the X server restarts.
        identity = await self.commands.execute(
            ["sh", "-c", "cat /proc/sys/kernel/random/boot_id && ps -C Xvfb -o pid=,lstart="],
            self.commands.configuration.request_timeout_seconds,
        )
        if len(identity.strip().splitlines()) < 2:
            raise EnvironmentError("Daytona desktop is unavailable.", code="environment_unavailable")
        return Geometry(active[0], hashlib.sha256(identity.encode()).hexdigest())

    async def describe(self) -> ComputerDescription:
        async with self.lock:
            geometry = await self.geometry()
            return ComputerDescription(
                targets=(
                    ComputerTarget(
                        target_id="display",
                        name="Daytona desktop",
                        width=geometry.display.width,
                        height=geometry.display.height,
                    ),
                ),
                observe_ready=True,
                input_ready=not self.blocked,
                scroll_units=("steps",),
            )

    async def observe(self, *, target_id: str | None = None, max_dimension: int = 1280) -> ComputerScreenshot:
        if target_id not in (None, "display") or not 256 <= max_dimension <= 2048:
            raise EnvironmentError("Invalid desktop observation request.", code="environment_request_invalid")
        async with self.lock:
            before = await self.geometry()
            result = decode_response(Screenshot, await self.toolbox.request("GET", self.path + "screenshot"), "daytona")
            try:
                data = base64.b64decode(result.screenshot, validate=True)
            except (ValueError, binascii.Error):
                raise EnvironmentError("Invalid screenshot encoding.", code="environment_provider_failure") from None
            if before != await self.geometry():
                raise EnvironmentError("Desktop changed during capture.", code="environment_stale_mount")
            encoded, width, height = await to_thread.run_sync(encode_image, data, before, max_dimension)
            value = ComputerObservation(
                execution_id=self.commands.execution_id,
                observed_generation=self.commands.generation,
                observation_id="screen-" + uuid4().hex,
                target_id="display",
                width=width,
                height=height,
                mime_type="image/jpeg",
                captured_at=datetime.now(UTC),
            )
            now = time.monotonic()
            self.observations = OrderedDict((k, v) for k, v in self.observations.items() if v.expires > now)
            while len(self.observations) >= 16:
                self.observations.popitem(last=False)
            self.observations[value.observation_id] = Observation(value, before, now + 120)
            return ComputerScreenshot(value, encoded)

    def prepare(self, request: ComputerInput) -> tuple[ComputerInput, Geometry | None]:
        basis = None
        points: dict[str, ComputerPoint] = {}
        if isinstance(request, (ComputerClick, ComputerMove, ComputerDrag, ComputerScroll)):
            stored = self.observations.get(request.observation.observation_id)
            if stored is None or stored.expires <= time.monotonic() or stored.value != request.observation:
                raise EnvironmentError("Desktop observation is stale.", code="environment_stale_mount")
            basis = stored.geometry
            for name in ("start", "end") if isinstance(request, ComputerDrag) else ("point",):
                point = getattr(request, name)
                if point.x >= stored.value.width or point.y >= stored.value.height:
                    raise EnvironmentError("Point is outside the screenshot.", code="environment_request_invalid")
                points[name] = ComputerPoint(
                    x=point.x * basis.display.width // stored.value.width,
                    y=point.y * basis.display.height // stored.value.height,
                )
        if isinstance(request, ComputerScroll) and (
            request.unit != "steps" or max(abs(request.delta_x), abs(request.delta_y)) > 100
        ):
            raise EnvironmentError(
                "Daytona supports at most 100 scroll steps per axis.", code="environment_unsupported"
            )
        if isinstance(request, ComputerTypeText) and len(request.text.encode()) > 16384:
            raise EnvironmentError("Text exceeds its byte limit.", code="environment_too_large")
        if isinstance(request, ComputerPressKeys) and (
            any(not re.fullmatch(r"[A-Za-z0-9_]+", key) for key in request.keys)
            or len(set(request.keys)) != len(request.keys)
        ):
            raise EnvironmentError("Invalid key chord.", code="environment_request_invalid")
        return request.model_copy(update=points), basis

    async def post(self, operation: str, body: dict[str, object]) -> None:
        result = await self.toolbox.request("POST", self.path + operation, body=body)
        if (
            not isinstance(result, dict)
            or result.get("success") is False
            or (operation == "mouse/scroll" and result.get("success") is not True)
        ):
            raise EnvironmentError("Desktop input was not confirmed.", code="environment_provider_failure")

    async def dispatch(self, request: ComputerInput) -> None:
        if isinstance(request, ComputerClick):
            await self.post(
                "mouse/click",
                {"x": request.point.x, "y": request.point.y, "button": request.button, "double": request.count == 2},
            )
        elif isinstance(request, ComputerMove):
            await self.post("mouse/move", {"x": request.point.x, "y": request.point.y})
        elif isinstance(request, ComputerDrag):
            try:
                await self.post("mouse/down", {"x": request.start.x, "y": request.start.y, "button": request.button})
                started = time.monotonic()
                for step in range(1, 11):
                    await asyncio.sleep(max(0, started + request.duration_ms / 1000 * step / 10 - time.monotonic()))
                    await self.post(
                        "mouse/move",
                        {
                            "x": request.start.x + (request.end.x - request.start.x) * step // 10,
                            "y": request.start.y + (request.end.y - request.start.y) * step // 10,
                        },
                    )
            finally:
                # Release at the current position even if press or movement failed.
                # This is best effort after a timeout: late upstream input remains uncertain.
                async with asyncio.timeout(5):
                    await self.post("mouse/up", {"button": request.button})
        elif isinstance(request, ComputerScroll):
            for delta, positive, negative in ((request.delta_y, "down", "up"), (request.delta_x, "right", "left")):
                if delta:
                    await self.post(
                        "mouse/scroll",
                        {
                            "x": request.point.x,
                            "y": request.point.y,
                            "direction": positive if delta > 0 else negative,
                            "amount": abs(delta),
                        },
                    )
        elif isinstance(request, ComputerTypeText):
            # Non-ASCII characters can reuse X11 key mappings before a GUI consumes
            # a bulk request. Separate them; use native Enter for every line break.
            for index, line in enumerate(request.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
                if index:
                    await self.post("keyboard/hotkey", {"keys": "enter"})
                for chunk in re.finditer(r"[\x00-\x7f]{1,25}|[^\x00-\x7f]", line):
                    await self.post("keyboard/type", {"text": chunk.group(), "delay": 50})
        elif isinstance(request, ComputerPressKeys):
            await self.post("keyboard/hotkey", {"keys": "+".join(request.keys)})

    async def bounded_dispatch(self, request: ComputerInput) -> None:
        async with asyncio.timeout(self.commands.configuration.request_timeout_seconds):
            await self.dispatch(request)

    async def close(self) -> None:
        async with self.lock:
            self.commands.closed = True
            self.observations.clear()

    async def execute(self, request: ComputerInput) -> ComputerActionResult:
        async with self.lock:
            self.check_open()
            if self.blocked:
                raise EnvironmentError(
                    "Reconcile prior input before continuing.",
                    code="environment_unavailable",
                    retry_hint="reconcile_first",
                )
            try:
                native, basis = self.prepare(request)
                if basis is not None and basis != await self.geometry():
                    raise EnvironmentError("Desktop observation is stale.", code="environment_stale_mount")
            except (EnvironmentError, EnvironmentProviderError) as error:
                raise EnvironmentError(
                    str(error) if isinstance(error, EnvironmentError) else "Desktop input preflight failed.",
                    code=error.code if isinstance(error, EnvironmentError) else "environment_unavailable",
                    details={"dispatch_stage": "pre_dispatch"},
                ) from error
            pending = asyncio.create_task(self.bounded_dispatch(native))
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                self.blocked = True
                # Keep the input lock and transport until the bounded dispatch/cleanup
                # finishes. Cancelling an HTTP waiter does not stop the guest action.
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
            except BaseException:
                # The API's client timeout/cancellation does not stop input on the server.
                self.blocked = True
                raise
            return ComputerActionResult(
                receipt=EnvironmentOperationReceipt(
                    execution_id=self.commands.execution_id,
                    observed_generation=self.commands.generation,
                    operation_id="operation-" + uuid4().hex,
                    stage="completed",
                    outcome="succeeded",
                ),
                effect="executed",
                input_cleanup_complete=True,
            )
