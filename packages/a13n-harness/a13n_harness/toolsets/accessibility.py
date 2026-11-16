"""Accessibility tools share Computer admission and retain references only for this Run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from pydantic_ai.tools import Tool

from a13n_harness.context import AgentContext
from a13n_harness.environment.accessibility import (
    AccessibilityActionReference,
    AccessibilityContent,
    AccessibilityCursor,
    AccessibilityDescription,
    AccessibilityFind,
    AccessibilityImageProjection,
    AccessibilityLimits,
    AccessibilityModel,
    AccessibilityNode,
    AccessibilityNodeReference,
    AccessibilityRangeQuery,
    AccessibilityRangeReadRequest,
    AccessibilityRangeReference,
    AccessibilityRangeResult,
    AccessibilityRead,
    AccessibilityRootScope,
    AccessibilityTree,
)
from a13n_harness.environment.accessibility_mutations import (
    AtspiMutation,
    AtspiRequest,
    AXMutation,
    AXRequest,
    UIAMutation,
    UIARequest,
)
from a13n_harness.environment.accessibility_operations import accessibility_mutation_requirements
from a13n_harness.environment.computer import ComputerObservation, ComputerPoint
from a13n_harness.environment.models import ACCESSIBILITY_MUTATION_ACTIONS, EnvironmentAction, EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.tools.metadata import HARNESS_TOOL_METADATA_KEY, HarnessToolMetadata, ToolOutputPolicy

from ._accessibility_references import AccessibilityReferences
from ._results import environment_failure


class _Describe(AccessibilityModel):
    alias: str | None = None


class _ScreenshotPoint(AccessibilityModel):
    kind: Literal["screenshot_point"] = "screenshot_point"
    observation_id: str
    point: ComputerPoint


class _Roots(_Describe):
    scope: Annotated[AccessibilityRootScope | _ScreenshotPoint, Field(discriminator="kind")] = Field(
        default_factory=AccessibilityRootScope
    )
    limits: AccessibilityLimits = Field(default_factory=AccessibilityLimits)


class _Node(AccessibilityModel):
    ref: AccessibilityNodeReference
    limits: AccessibilityLimits = Field(default_factory=AccessibilityLimits)


class _TreeRequest(_Node):
    view: Literal["control", "content", "raw"] = "control"
    continuation_ref: AccessibilityCursor | None = None


class _Inspect(_TreeRequest):
    projection_observation_id: str | None = None


class _Find(_TreeRequest):
    query: AccessibilityFind


class _Read(_Node):
    request: AccessibilityRead


class _Range(_Node):
    query: AccessibilityRangeQuery
    source: AccessibilityRangeReference | None = None


class _RangeRead(AccessibilityModel):
    range_ref: AccessibilityRangeReference
    request: AccessibilityRangeReadRequest
    limits: AccessibilityLimits = Field(default_factory=AccessibilityLimits)


class _AX(AccessibilityModel):
    ref: AccessibilityNodeReference
    request: AXMutation
    readback: bool = False


class _UIA(AccessibilityModel):
    ref: AccessibilityNodeReference
    request: UIAMutation
    readback: bool = False


class _Atspi(AccessibilityModel):
    ref: AccessibilityNodeReference
    request: AtspiMutation
    readback: bool = False


_READ_TOOLS = {
    "describe": _Describe,
    "roots": _Roots,
    "inspect": _Inspect,
    "find": _Find,
    "read": _Read,
    "range": _Range,
    "range_read": _RangeRead,
}
_MUTATION_TOOLS = {"ax": _AX, "uia": _UIA, "atspi": _Atspi}


class AccessibilityToolset:
    def __init__(
        self,
        environment: BoundEnvironment,
        *,
        observation: Callable[[str], ComputerObservation] | None = None,
        image_point: Callable[[ComputerPoint, ComputerObservation], ComputerPoint] | None = None,
        observation_reference: Callable[[str], str] | None = None,
    ) -> None:
        self._environment = environment
        self._observation = observation
        self._image_point = image_point
        self._observation_reference = observation_reference
        self._references = AccessibilityReferences()
        # One stable execution per mount in this Run bounds this map by mounts.
        # References derived by later reads retain that same execution identity.
        self._aliases: dict[str, str] = {}

    @staticmethod
    def available_names() -> frozenset[str]:
        return frozenset(f"computer_accessibility_{suffix}" for suffix in _READ_TOOLS) | frozenset(
            f"computer_{backend}" for backend in _MUTATION_TOOLS
        )

    def tools(self, *, allowed_names: frozenset[str] | None = None) -> list[Tool[AgentContext]]:
        actions = frozenset(
            action for mount in self._environment.snapshot.mounts for action in mount.permission_ceiling.operations
        )
        tools = []
        for suffix, model in _READ_TOOLS.items():
            name = f"computer_accessibility_{suffix}"
            action = EnvironmentAction(f"environment.computer.a11y_{suffix}")
            if action in actions and (allowed_names is None or name in allowed_names):
                tools.append(self._tool(name, action.value, model, getattr(self, f"_{suffix}"), read_only=True))
        for backend, model in _MUTATION_TOOLS.items():
            name = f"computer_{backend}"
            operations = frozenset(
                action.value.removeprefix(f"environment.computer.{backend}_")
                for action in actions & ACCESSIBILITY_MUTATION_ACTIONS
                if action.value.startswith(f"environment.computer.{backend}_")
            )
            if operations and (allowed_names is None or name in allowed_names):
                tools.append(
                    self._tool(
                        name,
                        f"environment.computer.{backend}",
                        model,
                        getattr(self, f"_{backend}"),
                        read_only=False,
                        allowed_ops=operations,
                    )
                )
        return tools

    def _tool(
        self,
        name: str,
        tool_id: str,
        model: type[BaseModel],
        handler: Callable[..., Awaitable[Any]],
        *,
        read_only: bool,
        allowed_ops: frozenset[str] | None = None,
    ) -> Tool[AgentContext]:
        schema, validate = self._references.arguments(model, allowed_ops=allowed_ops)

        async def invoke(**arguments: Any) -> Any:
            try:
                parsed = validate(arguments)
                return await handler(parsed)
            except ValidationError as error:
                return environment_failure(
                    EnvironmentError(
                        "Accessibility arguments are invalid.",
                        code="environment_request_invalid",
                        details={
                            "errors": [
                                {"type": item["type"], "message": item["msg"], "location": list(item["loc"])}
                                for item in error.errors(include_input=False, include_context=False)
                            ]
                        },
                    )
                )
            except EnvironmentError as error:
                return environment_failure(error)

        tool = Tool[AgentContext].from_schema(invoke, name=name, description=handler.__doc__, json_schema=schema)
        tool.metadata = {
            HARNESS_TOOL_METADATA_KEY: HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset({"read"})
                if read_only
                else frozenset({"read", "write", "delete", "execute", "external_communication"}),
                credential_audiences=(),
                idempotency="read_only" if read_only else "none",
                output_policy=ToolOutputPolicy(max_inline_bytes=262144, max_output_bytes=1048576, overflow="fail"),
            )
        }
        return tool

    def _encode(self, value: Any) -> Any:
        result = self._references.encode(value)
        self._capabilities(value, result)
        return result

    def _response(
        self,
        value: AccessibilityDescription | AccessibilityTree | AccessibilityContent | AccessibilityRangeResult,
        *,
        alias: str | None = None,
        node: AccessibilityNodeReference | None = None,
    ) -> dict[str, Any]:
        selected = self._aliases.get(node.execution_id) if node is not None else alias
        if node is None and selected is None:
            selected = self._environment.snapshot.default_mount
        backend = (
            value.backend if isinstance(value, AccessibilityDescription | AccessibilityTree) else value.node.backend
        )
        return {
            "ok": True,
            "alias": selected,
            "backend": backend,
            "reported_at": datetime.now(UTC).isoformat(),
            **self._encode(value),
        }

    def _capabilities(self, value: Any, result: Any) -> None:
        if isinstance(value, AccessibilityNode):
            alias = self._aliases.get(value.reference.execution_id)
            mount = next(
                (
                    item
                    for item in self._environment.snapshot.mounts
                    if item.name == alias and item.descriptor.generation == value.reference.observed_generation
                ),
                None,
            )
            for capability in result["capabilities"]:
                identifier = f"environment.computer.{value.reference.backend}_{capability['operation']}"
                capability["permitted"] = (
                    mount is not None
                    and identifier in ACCESSIBILITY_MUTATION_ACTIONS
                    and accessibility_mutation_requirements(EnvironmentAction(identifier))
                    <= mount.permission_ceiling.operations
                )
            if value.image_projection is not None:
                self._capabilities(value.image_projection, result["image_projection"])
        elif isinstance(value, AccessibilityImageProjection):
            if self._observation_reference is not None:
                result["source_observation_id"] = self._observation_reference(value.source_observation_id)
        elif isinstance(
            value,
            AccessibilityNodeReference
            | AccessibilityRangeReference
            | AccessibilityActionReference
            | AccessibilityCursor,
        ):
            return
        elif isinstance(value, BaseModel):
            for name in type(value).model_fields:
                self._capabilities(getattr(value, name), result[name])
        elif isinstance(value, tuple | list):
            for item, encoded in zip(value, result, strict=True):
                self._capabilities(item, encoded)

    async def _describe(self, args: _Describe) -> Any:
        """Describe the selected desktop's accessibility backend, readiness, methods and native frames."""
        result = await self._environment.computer.accessibility.describe(alias=args.alias)
        return self._response(result, alias=args.alias)

    async def _roots(self, args: _Roots) -> Any:
        """Acquire bounded accessibility roots on the selected mount without activating or focusing applications."""
        if isinstance(args.scope, _ScreenshotPoint):
            observation = self._get_observation(args.scope.observation_id)
            if self._image_point is None:
                raise EnvironmentError("Screenshot coordinates unavailable.", code="environment_unsupported")
            result = await self._environment.computer.accessibility.roots_at_point(
                observation, self._image_point(args.scope.point, observation), limits=args.limits, alias=args.alias
            )
            alias = self._aliases.get(observation.execution_id)
        else:
            result = await self._environment.computer.accessibility.roots(
                args.scope, limits=args.limits, alias=args.alias
            )
            alias = args.alias if args.alias is not None else self._environment.snapshot.default_mount
        if alias is not None:
            for node in result.nodes:
                self._aliases[node.reference.execution_id] = alias
        return self._response(result, alias=alias)

    async def _inspect(self, args: _Inspect) -> Any:
        """Inspect compact native nodes. Returned node/range/action/cursor references live only in this Run."""
        result = await self._environment.computer.accessibility.inspect(
            args.ref,
            limits=args.limits,
            view=args.view,
            cursor=args.continuation_ref,
            projection=self._get_observation(args.projection_observation_id)
            if args.projection_observation_id is not None
            else None,
        )
        return self._response(result, node=args.ref)

    def register_observation(self, observation: ComputerObservation, alias: str | None) -> None:
        if alias is not None:
            self._aliases[observation.execution_id] = alias

    def _get_observation(self, reference: str) -> ComputerObservation:
        if self._observation is None:
            raise EnvironmentError("Screenshot reference unavailable.", code="environment_not_found")
        return self._observation(reference)

    async def _find(self, args: _Find) -> Any:
        """Find nodes by bounded conjunction. Does not realize, expand, scroll or focus virtual items."""
        result = await self._environment.computer.accessibility.find(
            args.ref, args.query, limits=args.limits, view=args.view, cursor=args.continuation_ref
        )
        return self._response(result, node=args.ref)

    async def _read(self, args: _Read) -> Any:
        """Read explicit text, selection, table, document or native metadata; protected and empty are distinct."""
        result = await self._environment.computer.accessibility.read(args.ref, args.request, limits=args.limits)
        return self._response(result, node=args.ref)

    async def _range(self, args: _Range) -> Any:
        """Acquire or derive a native text range without changing UI selection. UIA endpoints are opaque."""
        result = await self._environment.computer.accessibility.range(
            args.ref, args.query, limits=args.limits, source=args.source
        )
        return self._response(result, node=args.ref)

    async def _range_read(self, args: _RangeRead) -> Any:
        """Read or compare retained native text ranges, with bounded text, attributes and geometry."""
        result = await self._environment.computer.accessibility.range_read(
            args.range_ref, args.request, limits=args.limits
        )
        return self._response(result, node=args.range_ref.node)

    async def _ax(self, args: _AX) -> Any:
        """Execute one advertised macOS AX operation. Readback requires inspect permission; never replay unknown effects."""
        return await self._execute(args.ref, AXRequest(request=args.request), args.readback)

    async def _uia(self, args: _UIA) -> Any:
        """Execute one Windows UIA operation on its original desktop. Native acknowledgment is not application-task success."""
        return await self._execute(args.ref, UIARequest(request=args.request), args.readback)

    async def _atspi(self, args: _Atspi) -> Any:
        """Execute one Linux AT-SPI operation. Text positions are native characters, not UTF-8 byte offsets."""
        return await self._execute(args.ref, AtspiRequest(request=args.request), args.readback)

    async def _execute(
        self, node: AccessibilityNodeReference, request: AXRequest | UIARequest | AtspiRequest, readback: bool
    ) -> Any:
        result = await self._environment.computer.accessibility.execute(node, request, readback=readback)
        response = {
            "alias": self._aliases.get(node.execution_id),
            "backend": node.backend,
            "reported_at": datetime.now(UTC).isoformat(),
            "ok": result.effect == "executed"
            and result.native_status == "returned_success"
            and result.dispatch_complete,
            "effect": result.effect,
            "native_status": result.native_status,
            "dispatch_complete": result.dispatch_complete,
            "application_outcome": result.application_outcome,
            "receipt": result.receipt.model_dump(mode="json"),
            "observation": self._encode(result.observation),
        }
        if result.effect in {"partial", "unknown"} or not result.dispatch_complete:
            response["hint"] = (
                "Inspect the application before another action; do not replay this operation or switch desktops."
            )
        return response
