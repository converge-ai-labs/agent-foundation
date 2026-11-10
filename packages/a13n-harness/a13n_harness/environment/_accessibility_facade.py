"""Accessibility dispatch keeps native references on their original Computer mount."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from a13n_harness.environment.accessibility import (
    AccessibilityActionReference,
    AccessibilityContent,
    AccessibilityCursor,
    AccessibilityDescription,
    AccessibilityFind,
    AccessibilityLimits,
    AccessibilityNode,
    AccessibilityNodeReference,
    AccessibilityRangeQuery,
    AccessibilityRangeReadRequest,
    AccessibilityRangeReference,
    AccessibilityRangeResult,
    AccessibilityRead,
    AccessibilityRootScope,
    AccessibilityScalar,
    AccessibilityScalarsValue,
    AccessibilityTextContent,
    AccessibilityTree,
    NativeSpan,
)
from a13n_harness.environment.accessibility_mutations import AccessibilityMutation, AccessibilityMutationResult
from a13n_harness.environment.accessibility_operations import (
    ProviderAccessibilityOperations,
    accessibility_mutation_action,
    accessibility_mutation_requirements,
)
from a13n_harness.environment.computer import ComputerCall, ComputerObservation, ComputerPoint
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError

from ._mount import _EnteredMount, _validate_provider_artifacts

if TYPE_CHECKING:
    from ._facades import _ComputerFacade
    from .coordinator import CompositeBoundEnvironment


def _node_references(value: Any) -> Iterator[AccessibilityNodeReference]:
    if isinstance(value, AccessibilityNodeReference):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _node_references(getattr(value, name))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _node_references(item)
    elif isinstance(value, tuple | list):
        for item in value:
            yield from _node_references(item)


def _validate_owners(node: AccessibilityNodeReference, value: Any) -> None:
    if isinstance(value, AccessibilityRangeReference | AccessibilityActionReference):
        owner = value.node
    elif isinstance(value, AccessibilityCursor):
        owner = value.root
    elif isinstance(value, NativeSpan):
        expected = {"ax": "ax_native", "atspi": "atspi_character"}.get(node.backend)
        if value.unit != expected:
            raise EnvironmentError("Text span does not match the native backend.", code="environment_request_invalid")
        return
    else:
        owner = None
    if owner is not None:
        if owner != node:
            raise EnvironmentError("Handle belongs to another node.", code="environment_request_invalid")
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _validate_owners(node, getattr(value, name))
    elif isinstance(value, tuple | list):
        for item in value:
            _validate_owners(node, item)


def _item_limits(value: Any, maximum: int) -> None:
    """Bound each returned item collection, including values nested in native properties."""
    if isinstance(value, AccessibilityNode):
        # State/capability inventories describe a node; they are not paged
        # content collections. Their schema and the total byte budget bound them.
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            if name != "field_errors":
                _item_limits(getattr(value, name), maximum)
    elif isinstance(value, tuple | list):
        if len(value) > maximum:
            raise EnvironmentError("Items exceed the requested limit.", code="environment_provider_failure")
        for item in value:
            _item_limits(item, maximum)


def _text_limits(value: Any, maximum: int) -> None:
    """Bound native text payloads, independently of metadata and opaque identifiers."""
    if isinstance(value, AccessibilityScalar):
        texts = (value.value,)
    elif isinstance(value, AccessibilityScalarsValue):
        texts = value.values
    elif isinstance(value, AccessibilityTextContent):
        texts = (value.text,)
    elif isinstance(value, AccessibilityNode):
        texts = (value.name, value.description, value.identifier)
    else:
        texts = ()
    if any(isinstance(text, str) and len(text.encode("utf-8")) > maximum for text in texts):
        raise EnvironmentError("Text exceeds the requested limit.", code="environment_provider_failure")
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _text_limits(getattr(value, name), maximum)
    elif isinstance(value, tuple | list):
        for item in value:
            _text_limits(item, maximum)


def _same_scope(node: AccessibilityNodeReference, value: Any, *, code: str) -> None:
    scope = (node.execution_id, node.observed_generation, node.backend, node.backend_epoch)
    for peer in _node_references(value):
        if (peer.execution_id, peer.observed_generation, peer.backend, peer.backend_epoch) != scope:
            raise EnvironmentError("Accessibility references belong to different execution epochs.", code=code)


class _AccessibilityFacade:
    def __init__(self, environment: CompositeBoundEnvironment, computer: _ComputerFacade) -> None:
        self._environment = environment
        self._computer = computer

    @asynccontextmanager
    async def _lease(
        self,
        action: EnvironmentAction,
        *,
        alias: str | None = None,
        node: AccessibilityNodeReference | None = None,
        peers: Any = None,
        observation: ComputerObservation | None = None,
    ) -> AsyncGenerator[tuple[_EnteredMount, ComputerCall, ProviderAccessibilityOperations]]:
        if observation is not None:
            entered = self._computer._for_observation(observation, action, alias)
            if node is not None and node.execution_id != observation.execution_id:
                raise EnvironmentError("Screenshot belongs to another mount.", code="environment_request_invalid")
        elif node is None:
            entered = self._environment._select_entered(alias)
        else:
            entered = self._environment._entered_for_execution(node.execution_id)
        if node is not None:
            _same_scope(node, peers, code="environment_request_invalid")
            _validate_owners(node, peers)
        async with self._computer._lease(entered, action, observation) as (entered, call):
            if node is not None and node.observed_generation != entered.public.descriptor.generation:
                raise EnvironmentError("Accessibility reference is stale.", code="environment_stale_mount")
            provider = entered.operations.accessibility
            if provider is None:
                raise EnvironmentError("Accessibility operations unavailable.", code="environment_unsupported")
            yield entered, call, provider

    def _result(
        self,
        entered: _EnteredMount,
        call: ComputerCall,
        value: AccessibilityTree | AccessibilityContent | AccessibilityRangeResult,
        limits: AccessibilityLimits,
        node: AccessibilityNodeReference | None = None,
    ) -> None:
        _validate_provider_artifacts(entered, value)
        if len(value.nodes) > limits.max_nodes:
            raise EnvironmentError("Nodes exceed the requested limit.", code="environment_provider_failure")
        if isinstance(value, AccessibilityTree):
            if any(item.reference.backend != value.backend for item in value.nodes):
                raise EnvironmentError("Invalid accessibility tree result.", code="environment_provider_failure")
            if value.nodes:
                _same_scope(value.nodes[0].reference, value, code="environment_provider_failure")
        else:
            if value.node != node:
                raise EnvironmentError("Result belongs to another node.", code="environment_provider_failure")
            _item_limits(value, limits.max_items)
        _text_limits(value, limits.max_text_bytes)
        if node is not None:
            _same_scope(node, value, code="environment_provider_failure")
            cursor = value.completeness.continuation
            if cursor is not None and cursor.root != node:
                raise EnvironmentError("Continuation belongs to another node.", code="environment_provider_failure")
        if len(value.model_dump_json().encode("utf-8")) > limits.max_result_bytes:
            raise EnvironmentError(
                "Accessibility provider exceeded the result byte limit.", code="environment_provider_failure"
            )
        call.result = value

    async def describe(self, *, alias: str | None = None) -> AccessibilityDescription:
        async with self._lease(EnvironmentAction.COMPUTER_A11Y_DESCRIBE, alias=alias) as (_, call, provider):
            result = await provider.describe()
            call.result = result
            return result

    async def roots(
        self,
        scope: AccessibilityRootScope,
        *,
        limits: AccessibilityLimits,
        alias: str | None = None,
    ) -> AccessibilityTree:
        async with self._lease(EnvironmentAction.COMPUTER_A11Y_ROOTS, alias=alias) as (entered, call, provider):
            result = await provider.roots(scope, limits=limits)
            self._result(entered, call, result, limits)
            return result

    async def roots_at_point(
        self,
        observation: ComputerObservation,
        point: ComputerPoint,
        *,
        limits: AccessibilityLimits,
        alias: str | None = None,
    ) -> AccessibilityTree:
        if point.x >= observation.width or point.y >= observation.height:
            raise EnvironmentError("Point is outside the screenshot.", code="environment_request_invalid")
        async with self._lease(EnvironmentAction.COMPUTER_A11Y_ROOTS, alias=alias, observation=observation) as (
            entered,
            call,
            provider,
        ):
            result = await provider.roots_at_point(observation, point, limits=limits)
            self._result(entered, call, result, limits)
            return result

    async def inspect(
        self,
        node: AccessibilityNodeReference,
        *,
        limits: AccessibilityLimits,
        view: Literal["control", "content", "raw"] = "control",
        cursor: AccessibilityCursor | None = None,
        projection: ComputerObservation | None = None,
    ) -> AccessibilityTree:
        async with self._lease(
            EnvironmentAction.COMPUTER_A11Y_INSPECT, node=node, peers=cursor, observation=projection
        ) as (
            entered,
            call,
            provider,
        ):
            result = await provider.inspect(node, limits=limits, view=view, cursor=cursor, projection=projection)
            for item in result.nodes:
                if item.image_projection is not None and (
                    projection is None or item.image_projection.source_observation_id != projection.observation_id
                ):
                    raise EnvironmentError(
                        "Projection belongs to another screenshot.", code="environment_provider_failure"
                    )
            self._result(entered, call, result, limits, node)
            return result

    async def find(
        self,
        node: AccessibilityNodeReference,
        query: AccessibilityFind,
        *,
        limits: AccessibilityLimits,
        view: Literal["control", "content", "raw"] = "control",
        cursor: AccessibilityCursor | None = None,
    ) -> AccessibilityTree:
        async with self._lease(EnvironmentAction.COMPUTER_A11Y_FIND, node=node, peers=cursor) as (
            entered,
            call,
            provider,
        ):
            result = await provider.find(node, query, limits=limits, view=view, cursor=cursor)
            if len(result.nodes) > limits.max_matches:
                raise EnvironmentError("Matches exceed the requested limit.", code="environment_provider_failure")
            self._result(entered, call, result, limits, node)
            return result

    async def read(
        self,
        node: AccessibilityNodeReference,
        request: AccessibilityRead,
        *,
        limits: AccessibilityLimits,
    ) -> AccessibilityContent:
        async with self._lease(EnvironmentAction.COMPUTER_A11Y_READ, node=node, peers=request) as (
            entered,
            call,
            provider,
        ):
            result = await provider.read(node, request, limits=limits)
            self._result(entered, call, result, limits, node)
            return result

    async def range(
        self,
        node: AccessibilityNodeReference,
        query: AccessibilityRangeQuery,
        *,
        limits: AccessibilityLimits,
        source: AccessibilityRangeReference | None = None,
    ) -> AccessibilityRangeResult:
        if source is not None and source.node != node:
            raise EnvironmentError("Source range belongs to another node.", code="environment_request_invalid")
        async with self._lease(EnvironmentAction.COMPUTER_A11Y_RANGE, node=node, peers=(query, source)) as (
            entered,
            call,
            provider,
        ):
            result = await provider.range(node, query, limits=limits, source=source)
            self._result(entered, call, result, limits, node)
            return result

    async def range_read(
        self,
        reference: AccessibilityRangeReference,
        request: AccessibilityRangeReadRequest,
        *,
        limits: AccessibilityLimits,
    ) -> AccessibilityRangeResult:
        async with self._lease(EnvironmentAction.COMPUTER_A11Y_RANGE_READ, node=reference.node, peers=request) as (
            entered,
            call,
            provider,
        ):
            result = await provider.range_read(reference, request, limits=limits)
            self._result(entered, call, result, limits, reference.node)
            return result

    async def execute(
        self, node: AccessibilityNodeReference, request: AccessibilityMutation, *, readback: bool = False
    ) -> AccessibilityMutationResult:
        if request.backend != node.backend:
            raise EnvironmentError("Native operation does not match the node backend.", code="environment_unsupported")
        action = accessibility_mutation_action(request)
        async with self._lease(action, node=node, peers=request) as (entered, call, provider):
            for required in accessibility_mutation_requirements(action, readback=readback):
                self._environment.require_action(entered.mount_id, required)
            result = await provider.execute(node, request, readback=readback)
            evidence: dict[str, Any] = {"dispatch_stage": "unknown"}
            try:
                _validate_provider_artifacts(entered, result.receipt)
                evidence["receipt"] = result.receipt.model_dump(mode="json")
                _validate_provider_artifacts(entered, result.observation)
                _same_scope(node, result.observation, code="environment_provider_failure")
                if result.observation is not None and (not readback or result.observation.reference != node):
                    raise EnvironmentError("Unrequested or mismatched readback.", code="environment_provider_failure")
            except EnvironmentError as error:
                raise EnvironmentError(
                    "Accessibility mutation returned unconfirmed evidence.",
                    code="environment_unknown_outcome",
                    retry_hint="reconcile_first",
                    details=evidence,
                ) from error
            call.result = result
            return result
