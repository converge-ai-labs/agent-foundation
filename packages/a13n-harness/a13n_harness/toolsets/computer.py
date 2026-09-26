"""Model-facing computer tools over explicitly authorized Environment mounts."""

from __future__ import annotations

import secrets
from collections import OrderedDict
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.providers.environment.computer import (
    ComputerButton,
    ComputerClick,
    ComputerDrag,
    ComputerInput,
    ComputerMove,
    ComputerObservation,
    ComputerPoint,
    ComputerPressKeys,
    ComputerScroll,
    ComputerTypeText,
)
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy

from ._results import environment_failure


class ComputerToolset:
    """Run-local observation references; never acquire or reserve a desktop."""

    def __init__(self, environment: BoundEnvironment) -> None:
        self._environment = environment
        self._observations: OrderedDict[str, ComputerObservation] = OrderedDict()

    def get_toolset(self, *, allowed_names: frozenset[str] | None = None) -> FunctionToolset[AgentContext]:
        actions = frozenset(
            action for mount in self._environment.snapshot.mounts for action in mount.permission_ceiling.operations
        )
        methods = (
            (self.computer_describe, EnvironmentAction.COMPUTER_DESCRIBE, True),
            (self.computer_observe, EnvironmentAction.COMPUTER_OBSERVE, True),
            (self.computer_click, EnvironmentAction.COMPUTER_CLICK, False),
            (self.computer_move, EnvironmentAction.COMPUTER_MOVE, False),
            (self.computer_drag, EnvironmentAction.COMPUTER_DRAG, False),
            (self.computer_scroll, EnvironmentAction.COMPUTER_SCROLL, False),
            (self.computer_type_text, EnvironmentAction.COMPUTER_TYPE_TEXT, False),
            (self.computer_press_keys, EnvironmentAction.COMPUTER_PRESS_KEYS, False),
        )
        return FunctionToolset(
            tools=[
                HarnessTool(
                    method,
                    harness_metadata=HarnessToolMetadata(
                        tool_id=action.value,
                        effects=frozenset({"read"})
                        if read_only
                        else frozenset({"read", "write", "delete", "execute", "external_communication"}),
                        credential_audiences=(),
                        idempotency="read_only" if read_only else "none",
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=65536, max_output_bytes=5 * 1024 * 1024, overflow="fail"
                        ),
                    ),
                )
                for method, action, read_only in methods
                if action in actions and (allowed_names is None or method.__name__ in allowed_names)
            ],
            id="a13n-computer-tools",
        )

    async def computer_describe(self, *, alias: str | None = None) -> Any:
        """List desktop targets and current screen-recording/input readiness on a selected mount."""
        try:
            result = await self._environment.computer.describe(alias=alias)
            return {"ok": True, **result.model_dump(mode="json")}
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_observe(
        self,
        *,
        alias: str | None = None,
        target_id: str | None = None,
        max_dimension: Annotated[int, Field(ge=256, le=2048)] = 1280,
    ) -> Any:
        """Capture a desktop image. Use returned observation_id and image pixel coordinates for pointer input.

        A reference binds target geometry, not screen freshness. Other users and agents may change the shared GUI.
        """
        try:
            screenshot = await self._environment.computer.observe(
                alias=alias, target_id=target_id, max_dimension=max_dimension
            )
            reference = f"obs-{secrets.token_hex(4)}"
            self._observations[reference] = screenshot.observation
            while len(self._observations) > 16:
                self._observations.popitem(last=False)
            return ToolReturn(
                return_value={
                    "ok": True,
                    **screenshot.observation.model_dump(mode="json", exclude={"mount_id", "observed_generation"}),
                    "observation_id": reference,
                },
                content=[
                    BinaryContent(
                        data=screenshot.data,
                        media_type=screenshot.observation.mime_type,
                        vendor_metadata={"display": False},
                    )
                ],
                metadata={"a13n.computer.screenshot": True},
            )
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_click(
        self,
        observation_id: str,
        point: ComputerPoint,
        *,
        button: ComputerButton = "left",
        count: Annotated[int, Field(ge=1, le=2)] = 1,
    ) -> Any:
        """Click an image-pixel position on a previously observed target. Never replay an unknown outcome."""
        try:
            return await self._execute(
                ComputerClick(observation=self._observation(observation_id), point=point, button=button, count=count)
            )
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_move(self, observation_id: str, point: ComputerPoint) -> Any:
        """Move the pointer to an image-pixel position on the observed target."""
        try:
            return await self._execute(ComputerMove(observation=self._observation(observation_id), point=point))
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_drag(
        self,
        observation_id: str,
        start: ComputerPoint,
        end: ComputerPoint,
        *,
        button: ComputerButton = "left",
        duration_ms: Annotated[int, Field(ge=100, le=3000)] = 500,
    ) -> Any:
        """Perform one bounded drag with automatic button release, using image pixels."""
        try:
            return await self._execute(
                ComputerDrag(
                    observation=self._observation(observation_id),
                    start=start,
                    end=end,
                    button=button,
                    duration_ms=duration_ms,
                )
            )
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_scroll(
        self,
        observation_id: str,
        point: ComputerPoint,
        *,
        delta_x: Annotated[int, Field(ge=-10000, le=10000)] = 0,
        delta_y: Annotated[int, Field(ge=-10000, le=10000)] = 0,
    ) -> Any:
        """Scroll at an image-pixel position. Positive deltas scroll right/down."""
        try:
            return await self._execute(
                ComputerScroll(
                    observation=self._observation(observation_id), point=point, delta_x=delta_x, delta_y=delta_y
                )
            )
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_type_text(
        self, text: Annotated[str, Field(min_length=1, max_length=16384)], *, alias: str | None = None
    ) -> Any:
        """Type 1-16384 UTF-8 bytes into current foreground focus; this does not select or focus a window."""
        return await self._execute(ComputerTypeText(text=text), alias=alias)

    async def computer_press_keys(
        self,
        keys: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)],
        *,
        alias: str | None = None,
    ) -> Any:
        """Press a key combination in current foreground focus, then release all keys.

        Keys include lowercase letters, digits, meta (Command), control, alt (Option), shift, enter,
        tab, escape, space, backspace, delete, left, right, up, down, home, end, page_up, page_down,
        and f1 through f12.
        """
        return await self._execute(ComputerPressKeys(keys=keys), alias=alias)

    def _observation(self, reference: str) -> ComputerObservation:
        observation = self._observations.get(reference)
        if observation is None:
            raise EnvironmentError("Observation is unavailable; capture a new image.", code="environment_not_found")
        return observation

    async def _execute(self, request: ComputerInput, *, alias: str | None = None) -> Any:
        try:
            result = await self._environment.computer.execute(request, alias=alias)
            return {
                "ok": result.effect == "executed" and result.input_cleanup_complete,
                "effect": result.effect,
                "input_cleanup_complete": result.input_cleanup_complete,
                "receipt": result.receipt.model_dump(mode="json"),
            }
        except EnvironmentError as error:
            return environment_failure(error)
