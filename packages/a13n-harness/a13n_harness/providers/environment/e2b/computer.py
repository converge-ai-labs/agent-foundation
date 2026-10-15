"""Computer facet over E2B's native authenticated command/file channel."""

from __future__ import annotations

import asyncio
import io
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from anyio import to_thread
from PIL import Image

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
from a13n_harness.environment.models import EnvironmentError, EnvironmentOperationReceipt
from a13n_harness.providers.environment.e2b.commands import GuestCommands
from a13n_harness.providers.environment.e2b.desktop import Desktop, DesktopDescription

_MAX_IMAGE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _Observation:
    observation: ComputerObservation
    desktop: DesktopDescription
    expires: float


def _image(data: bytes, description: DesktopDescription, maximum: int) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(data)) as source:
        if source.size != (description.width, description.height):
            raise EnvironmentError("E2B screenshot geometry changed.", code="environment_stale_mount")
        image = source.convert("RGB")
        image.thumbnail((maximum, maximum))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
        encoded = output.getvalue()
        if len(encoded) > _MAX_IMAGE_BYTES:
            raise EnvironmentError("E2B screenshot is too large.", code="environment_too_large")
        return encoded, image.width, image.height


class E2BComputerOperations:
    def __init__(self, commands: GuestCommands) -> None:
        self.commands = commands
        self.desktop = Desktop(commands.sandbox, commands.configuration)
        self._observations: OrderedDict[str, _Observation] = OrderedDict()
        self._lock = asyncio.Lock()
        self._blocked = False

    def _check_open(self) -> None:
        if self.commands.closed:
            raise EnvironmentError("E2B execution is closed.", code="environment_closed")

    async def describe(self) -> ComputerDescription:
        async with self._lock:
            self._check_open()
            value = await self.desktop.describe()
            return ComputerDescription(
                targets=(
                    ComputerTarget(target_id="display", name="E2B desktop", width=value.width, height=value.height),
                ),
                observe_ready=True,
                input_ready=not self._blocked,
                scroll_units=("steps",),
            )

    async def observe(self, *, target_id: str | None = None, max_dimension: int = 1280) -> ComputerScreenshot:
        self._check_open()
        if target_id not in (None, "display") or not 256 <= max_dimension <= 2048:
            raise EnvironmentError("Invalid desktop observation request.", code="environment_request_invalid")
        async with self._lock:
            self._check_open()
            description, data = await self.desktop.observe()
            encoded, width, height = await to_thread.run_sync(_image, data, description, max_dimension)
            observation = ComputerObservation(
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
            self._observations = OrderedDict(
                (key, item) for key, item in self._observations.items() if item.expires > now
            )
            while len(self._observations) >= 16:
                self._observations.popitem(last=False)
            self._observations[observation.observation_id] = _Observation(observation, description, now + 120)
            return ComputerScreenshot(observation, encoded)

    def _request(self, request: ComputerInput) -> tuple[ComputerInput, DesktopDescription | None]:
        basis: DesktopDescription | None = None
        points: dict[str, ComputerPoint] = {}
        if isinstance(request, (ComputerClick, ComputerMove, ComputerDrag, ComputerScroll)):
            stored = self._observations.get(request.observation.observation_id)
            if stored is None or stored.expires <= time.monotonic() or stored.observation != request.observation:
                raise EnvironmentError("E2B screenshot reference is stale.", code="environment_stale_mount")
            basis = stored.desktop
            for name in ("start", "end") if isinstance(request, ComputerDrag) else ("point",):
                point = getattr(request, name)
                if point.x >= stored.observation.width or point.y >= stored.observation.height:
                    raise EnvironmentError("Point is outside the screenshot.", code="environment_request_invalid")
                points[name] = ComputerPoint(
                    x=min(stored.desktop.width - 1, point.x * stored.desktop.width // stored.observation.width),
                    y=min(stored.desktop.height - 1, point.y * stored.desktop.height // stored.observation.height),
                )
        if isinstance(request, ComputerScroll) and (
            request.unit != "steps" or request.delta_x != 0 or abs(request.delta_y) > 100
        ):
            raise EnvironmentError(
                "E2B Desktop SDK supports vertical step scrolling only (at most 100 steps).",
                code="environment_unsupported",
            )
        if isinstance(request, ComputerTypeText) and len(request.text.encode()) > 16384:
            raise EnvironmentError("Text exceeds the UTF-8 byte limit.", code="environment_too_large")
        if isinstance(request, ComputerPressKeys):
            from e2b_desktop.main import KEYS

            # The SDK interpolates key names into a command: only its fixed
            # names and single ASCII alphanumerics are admitted.
            allowed = set(KEYS) | set("abcdefghijklmnopqrstuvwxyz0123456789")
            if any(key not in allowed for key in request.keys) or len(set(request.keys)) != len(request.keys):
                raise EnvironmentError("Unsupported key chord.", code="environment_request_invalid")
        return request.model_copy(update=points), basis

    async def execute(self, request: ComputerInput) -> ComputerActionResult:
        self._check_open()
        async with self._lock:
            self._check_open()
            if self._blocked:
                raise EnvironmentError(
                    "Reconcile the previous input before further input.",
                    code="environment_unavailable",
                    retry_hint="reconcile_first",
                )
            native, basis = self._request(request)
            operation_id = "operation-" + uuid4().hex
            try:
                await self.desktop.execute(native, basis)
            except EnvironmentError as error:
                if error.details.get("dispatch_stage") != "pre_dispatch":
                    self._blocked = True
                raise
            except BaseException:
                self._blocked = True
                raise
            return ComputerActionResult(
                receipt=EnvironmentOperationReceipt(
                    execution_id=self.commands.execution_id,
                    observed_generation=self.commands.generation,
                    operation_id=operation_id,
                    stage="completed",
                    outcome="succeeded",
                ),
                effect="executed",
                input_cleanup_complete=True,
            )

    async def close(self) -> None:
        async with self._lock:
            self._observations.clear()
            await self.desktop.close()
