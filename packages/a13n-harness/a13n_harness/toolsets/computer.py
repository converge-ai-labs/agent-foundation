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

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import environment_failure

_ComputerAlias = Annotated[
    str | None,
    Field(
        description=(
            "Existing Environment mount name with the required computer action. Omit only to use the current "
            "default mount; this does not inherit the mount of a previous screenshot or click."
        )
    ),
]
_ObservationReference = Annotated[
    str,
    Field(
        description="obs- reference returned by computer_observe in this Run; selects its original mount and target."
    ),
]


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
        return InstructionFunctionToolset(
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
            instructions=[tool_instruction("environment-computer")],
        )

    async def computer_describe(self, *, alias: _ComputerAlias = None) -> Any:
        """List desktop targets and current screen-recording/input readiness on a selected mount."""
        try:
            selected_alias = alias if alias is not None else self._environment.snapshot.default_mount
            result = await self._environment.computer.describe(alias=selected_alias)
            return {"ok": True, **result.model_dump(mode="json"), "alias": selected_alias}
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_observe(
        self,
        *,
        alias: _ComputerAlias = None,
        target_id: Annotated[
            str | None,
            Field(
                description="Target ID from computer_describe on the same mount. Omit for the provider's default target."
            ),
        ] = None,
        max_dimension: Annotated[int, Field(ge=256, le=2048)] = 1280,
    ) -> Any:
        """Capture a desktop image. Use returned observation_id and image pixel coordinates for pointer input.

        A reference binds target geometry, not screen freshness. Other users and agents may change the shared GUI.
        """
        try:
            selected_alias = alias if alias is not None else self._environment.snapshot.default_mount
            screenshot = await self._environment.computer.observe(
                alias=selected_alias, target_id=target_id, max_dimension=max_dimension
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
                    "alias": selected_alias,
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
        observation_id: _ObservationReference,
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

    async def computer_move(self, observation_id: _ObservationReference, point: ComputerPoint) -> Any:
        """Move the pointer to an image-pixel position on the observed target."""
        try:
            return await self._execute(ComputerMove(observation=self._observation(observation_id), point=point))
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_drag(
        self,
        observation_id: _ObservationReference,
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
        observation_id: _ObservationReference,
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
        self,
        text: Annotated[
            str,
            Field(
                min_length=1,
                max_length=16384,
                description=(
                    "Literal text, limited to 16384 UTF-8 bytes. Non-ASCII characters may use multiple bytes; "
                    "the schema's maxLength is only a character ceiling, not the byte budget."
                ),
            ),
        ],
        *,
        alias: _ComputerAlias = None,
    ) -> Any:
        """Type 1-16384 UTF-8 bytes into the selected mount's foreground focus, without focusing a window.

        Pass alias explicitly to keep typing on the intended desktop; a preceding click does not select this mount.
        """
        return await self._execute(ComputerTypeText(text=text), alias=alias)

    async def computer_press_keys(
        self,
        keys: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)],
        *,
        alias: _ComputerAlias = None,
    ) -> Any:
        """Press keys in the selected mount's foreground focus, then release them. Prefer an explicit alias.

        Keys include lowercase letters, digits, meta (Command), control, alt (Option), shift, enter,
        tab, escape, space, backspace, delete, left, right, up, down, home, end, page_up, page_down,
        and f1 through f12.
        """
        return await self._execute(ComputerPressKeys(keys=keys), alias=alias)

    def _observation(self, reference: str) -> ComputerObservation:
        observation = self._observations.get(reference)
        if observation is None:
            raise EnvironmentError(
                "Observation is unavailable; capture a new image.",
                code="environment_not_found",
                details={
                    "field": "observation_id",
                    "reason": "observation_unavailable",
                    "hint": (
                        "Use computer_observe with the intended alias to capture a new image, reassess the GUI, "
                        "and use its returned observation_id. References from earlier Runs or evicted observations "
                        "cannot be reused. No input was dispatched."
                    ),
                },
            )
        return observation

    async def _execute(self, request: ComputerInput, *, alias: _ComputerAlias = None) -> Any:
        try:
            result = await self._environment.computer.execute(request, alias=alias)
            return {
                "ok": result.effect == "executed" and result.input_cleanup_complete,
                "effect": result.effect,
                "input_cleanup_complete": result.input_cleanup_complete,
                "receipt": result.receipt.model_dump(mode="json"),
            }
        except EnvironmentError as error:
            if isinstance(request, ComputerClick | ComputerMove | ComputerDrag | ComputerScroll) and error.code in {
                "environment_stale_mount",
                "environment_selection_invalid",
            }:
                error = EnvironmentError(
                    "The observed desktop is no longer selectable.",
                    code=error.code,
                    retry_hint=error.retry_hint,
                    details={
                        **error.details,
                        "field": "observation_id",
                        "reason": "observation_stale",
                        "hint": (
                            "Check the intended mount in the latest Environment context, then use computer_observe "
                            "with its alias and reassess the GUI. Do not reuse this observation_id or blindly replay "
                            "the input."
                        ),
                    },
                )
            return environment_failure(error)
