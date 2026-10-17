"""Model-facing computer tools over explicitly authorized Environment mounts."""

from __future__ import annotations

import secrets
from collections import OrderedDict
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.environment.computer import (
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
from a13n_harness.environment.configuration import ComputerConfiguration, ComputerCoordinateSpace
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
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

    def __init__(self, environment: BoundEnvironment, *, coordinate_space: ComputerCoordinateSpace = "pixels") -> None:
        self._coordinate_space = ComputerConfiguration(coordinate_space=coordinate_space).coordinate_space
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
            instructions=[tool_instruction("environment-computer"), self._coordinate_instruction()],
        )

    async def computer_describe(self, *, alias: _ComputerAlias = None) -> Any:
        """List desktop targets and current screen-recording/input readiness on a selected mount."""
        try:
            selected_alias = alias if alias is not None else self._environment.snapshot.default_mount
            result = await self._environment.computer.describe(alias=selected_alias)
            return {
                "ok": True,
                **result.model_dump(mode="json"),
                "alias": selected_alias,
                "coordinate_space": self._coordinate_space,
            }
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
        """Capture a desktop image. Use returned observation_id and coordinate_space for pointer input.

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
                    "coordinate_space": self._coordinate_space,
                },
                content=[
                    BinaryContent(
                        data=screenshot.data,
                        media_type=screenshot.observation.mime_type,
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
        """Click a position in the configured coordinate space on a previously observed target. Never replay an unknown outcome."""
        try:
            observation = self._observation(observation_id)
            return await self._execute(
                ComputerClick(
                    observation=observation, point=self._point(point, observation), button=button, count=count
                )
            )
        except EnvironmentError as error:
            return environment_failure(error)

    async def computer_move(self, observation_id: _ObservationReference, point: ComputerPoint) -> Any:
        """Move the pointer to a position in the configured coordinate space on the observed target."""
        try:
            observation = self._observation(observation_id)
            return await self._execute(ComputerMove(observation=observation, point=self._point(point, observation)))
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
        """Perform one bounded drag with automatic button release, using the configured coordinate space."""
        try:
            observation = self._observation(observation_id)
            return await self._execute(
                ComputerDrag(
                    observation=observation,
                    start=self._point(start, observation),
                    end=self._point(end, observation),
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
        unit: Literal["pixels", "steps"] = "pixels",
    ) -> Any:
        """Scroll right/down for positive deltas. Use a unit advertised by computer_describe.

        X11 requires steps (at most 100 per axis); pixels remain the default (at most 10000 per axis).
        """
        try:
            observation = self._observation(observation_id)
            return await self._execute(
                ComputerScroll(
                    observation=observation,
                    point=self._point(point, observation),
                    delta_x=delta_x,
                    delta_y=delta_y,
                    unit=unit,
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
        Availability is mount-specific: Envd Linux X11 omits literal text entry; native E2B desktops support it.
        Key chords are not a literal-text substitute.
        """
        return await self._execute(ComputerTypeText(text=text), alias=alias)

    async def computer_press_keys(
        self,
        keys: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)],
        *,
        alias: _ComputerAlias = None,
    ) -> Any:
        """Press keys in the selected mount's foreground focus, then release them. Prefer an explicit alias.

        Keys include lowercase letters, digits, meta (Command/Super), control, alt (Option/Alt), shift, enter,
        tab, escape, space, backspace, delete, left, right, up, down, home, end, page_up, page_down,
        and f1 through f12.
        """
        return await self._execute(ComputerPressKeys(keys=keys), alias=alias)

    def _coordinate_instruction(self) -> str:
        if self._coordinate_space == "normalized_1000":
            return (
                "Computer coordinate_space is normalized_1000. For click, move, drag endpoints and scroll pointer "
                "positions, supply relative integer coordinates from 0 to 1000 on EACH axis: (0,0) is top-left "
                "and (1000,1000) is bottom-right. The tool converts coordinates to screenshot pixels. "
                "Do NOT multiply by image width or height yourself. Scroll deltas keep their declared units."
            )
        return "Computer coordinate_space is pixels. Supply positions in the returned screenshot's width and height."

    def _point(self, point: ComputerPoint, observation: ComputerObservation) -> ComputerPoint:
        if self._coordinate_space == "pixels":
            return point
        if point.x > 1000 or point.y > 1000:
            raise EnvironmentError(
                "Relative pointer coordinates must be between 0 and 1000.",
                code="environment_request_invalid",
                details={
                    "field": "point",
                    "coordinate_space": self._coordinate_space,
                    "dispatch_stage": "pre_dispatch",
                },
            )
        # Use this observation's image geometry; providers own the image-to-display mapping.
        return ComputerPoint(
            x=min(observation.width - 1, point.x * observation.width // 1000),
            y=min(observation.height - 1, point.y * observation.height // 1000),
        )

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
            response: dict[str, Any] = {
                "ok": result.effect == "executed" and result.input_cleanup_complete,
                "effect": result.effect,
                "input_cleanup_complete": result.input_cleanup_complete,
                "receipt": result.receipt.model_dump(mode="json"),
            }
            if result.effect in {"partial", "unknown"} or not result.input_cleanup_complete:
                response["hint"] = (
                    "Input may already have affected the shared desktop. Capture a fresh image and inspect "
                    "the application before another action; do not automatically replay the gesture or switch "
                    "desktops. A new Run or Session does not undo prior input."
                )
                if not result.input_cleanup_complete:
                    response["hint"] += " Input cleanup failed; keys or mouse buttons may remain held."
            return response
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
            elif (
                isinstance(request, ComputerTypeText)
                and error.code == "environment_denied"
                and error.details.get("reason") == "mount_action_denied"
            ):
                error = EnvironmentError(
                    "Literal text entry is unavailable on the selected mount.",
                    code=error.code,
                    retry_hint=error.retry_hint,
                    details={
                        **error.details,
                        "field": "alias",
                        "dispatch_stage": "pre_dispatch",
                        "hint": (
                            f"{error.details.get('hint', '')} "
                            "No text input was dispatched. computer_type_text being visible does not mean it is "
                            "available on this alias. Do not retry unchanged or automatically switch desktops. "
                            "computer_press_keys sends physical key chords, not literal text. If text entry is "
                            "needed on this desktop, explain the limitation to the user."
                        ),
                    },
                )
            return environment_failure(error)
