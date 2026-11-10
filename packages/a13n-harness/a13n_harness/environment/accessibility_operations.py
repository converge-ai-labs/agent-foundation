"""Provider accessibility facet within the Computer operation family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from a13n_harness.environment.accessibility import (
    AccessibilityContent,
    AccessibilityCursor,
    AccessibilityDescription,
    AccessibilityFind,
    AccessibilityLimits,
    AccessibilityNodeReference,
    AccessibilityRangeQuery,
    AccessibilityRangeReadRequest,
    AccessibilityRangeReference,
    AccessibilityRangeResult,
    AccessibilityRead,
    AccessibilityRootScope,
    AccessibilityTree,
)
from a13n_harness.environment.accessibility_mutations import AccessibilityMutation, AccessibilityMutationResult
from a13n_harness.environment.models import EnvironmentAction

if TYPE_CHECKING:
    from a13n_harness.environment.computer import ComputerObservation, ComputerPoint


def accessibility_mutation_action(request: AccessibilityMutation) -> EnvironmentAction:
    return EnvironmentAction(f"environment.computer.{request.backend}_{request.request.op}")


def accessibility_mutation_requirements(
    action: EnvironmentAction, *, readback: bool = False
) -> frozenset[EnvironmentAction]:
    """Exact authority for a native mutation, including its optional readback."""
    required = {action}
    if action in {EnvironmentAction.COMPUTER_ATSPI_TEXT_COPY, EnvironmentAction.COMPUTER_ATSPI_TEXT_CUT}:
        required.add(EnvironmentAction.COMPUTER_WRITE_CLIPBOARD)
    elif action == EnvironmentAction.COMPUTER_ATSPI_TEXT_PASTE:
        required.add(EnvironmentAction.COMPUTER_READ_CLIPBOARD)
    if readback:
        required.add(EnvironmentAction.COMPUTER_A11Y_INSPECT)
    return frozenset(required)


class ProviderAccessibilityOperations(Protocol):
    """Native implementation facts; optional independently of physical Computer I/O."""

    async def describe(self) -> AccessibilityDescription: ...

    async def roots(self, scope: AccessibilityRootScope, *, limits: AccessibilityLimits) -> AccessibilityTree: ...

    async def roots_at_point(
        self, observation: ComputerObservation, point: ComputerPoint, *, limits: AccessibilityLimits
    ) -> AccessibilityTree: ...

    async def inspect(
        self,
        node: AccessibilityNodeReference,
        *,
        limits: AccessibilityLimits,
        view: Literal["control", "content", "raw"] = "control",
        cursor: AccessibilityCursor | None = None,
        projection: ComputerObservation | None = None,
    ) -> AccessibilityTree: ...

    async def find(
        self,
        node: AccessibilityNodeReference,
        query: AccessibilityFind,
        *,
        limits: AccessibilityLimits,
        view: Literal["control", "content", "raw"] = "control",
        cursor: AccessibilityCursor | None = None,
    ) -> AccessibilityTree: ...

    async def read(
        self,
        node: AccessibilityNodeReference,
        request: AccessibilityRead,
        *,
        limits: AccessibilityLimits,
    ) -> AccessibilityContent: ...

    async def range(
        self,
        node: AccessibilityNodeReference,
        query: AccessibilityRangeQuery,
        *,
        limits: AccessibilityLimits,
        source: AccessibilityRangeReference | None = None,
    ) -> AccessibilityRangeResult: ...

    async def range_read(
        self,
        reference: AccessibilityRangeReference,
        request: AccessibilityRangeReadRequest,
        *,
        limits: AccessibilityLimits,
    ) -> AccessibilityRangeResult: ...

    async def execute(
        self,
        node: AccessibilityNodeReference,
        request: AccessibilityMutation,
        *,
        readback: bool = False,
    ) -> AccessibilityMutationResult: ...


class BoundAccessibilityOperations(ProviderAccessibilityOperations, Protocol):
    async def roots_at_point(
        self,
        observation: ComputerObservation,
        point: ComputerPoint,
        *,
        limits: AccessibilityLimits,
        alias: str | None = None,
    ) -> AccessibilityTree: ...

    async def describe(self, *, alias: str | None = None) -> AccessibilityDescription: ...

    async def roots(
        self,
        scope: AccessibilityRootScope,
        *,
        limits: AccessibilityLimits,
        alias: str | None = None,
    ) -> AccessibilityTree: ...
