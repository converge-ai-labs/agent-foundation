"""Immutable authorization catalog for declarative custom Capability types."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import CAPABILITY_TYPES, AbstractCapability

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError


def _validate_capability_id(
    capability_id: object,
    *,
    capability_type: type[object],
    source: str,
) -> str:
    if not isinstance(capability_id, str) or not capability_id.strip() or ":" in capability_id:
        raise DefinitionError(
            "Capability IDs must be non-blank strings without ':' when present.",
            code="capability_id_invalid",
            details={
                "capability_type": capability_type.__name__,
                "source": source,
            },
        )
    return capability_id


def first_party_declarative_capability_types() -> tuple[type[AbstractCapability[AgentContext]], ...]:
    """Return Harness-owned Capability types accepted from AgentSpec."""
    from a13n_harness.capabilities.shell_review import ShellReviewCapability

    return (ShellReviewCapability,)


def _reserved_harness_capability_contract() -> tuple[
    tuple[type[AbstractCapability[AgentContext]], ...], frozenset[str]
]:
    from a13n_harness.capabilities.codeact import CODEACT_CAPABILITY_ID, CodeActCapability
    from a13n_harness.capabilities.delegation import (
        DELEGATION_CAPABILITY_ID,
        DELEGATION_RUN_CAPABILITY_ID,
        DelegationCapability,
        DelegationRunCapability,
    )
    from a13n_harness.capabilities.lifecycle import (
        LIFECYCLE_EVENT_CAPABILITY_ID,
        LifecycleEventCapability,
    )
    from a13n_harness.capabilities.shell_review import (
        SHELL_REVIEW_CAPABILITY_ID,
        ShellReviewCapability,
    )
    from a13n_harness.environment.dynamic import (
        DYNAMIC_ENVIRONMENT_CAPABILITY_ID,
        DynamicEnvironmentCapability,
    )
    from a13n_harness.filters.integrity import (
        MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID,
        MessageIntegrityFilterCapability,
    )
    from a13n_harness.model_context import (
        MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID,
        ModelContextCoordinatorCapability,
    )
    from a13n_harness.pricing import (
        MODEL_COST_CAPABILITY_ID,
        AbstractModelCostCapability,
    )
    from a13n_harness.tools.client import (
        CLIENT_TOOLS_CAPABILITY_ID,
        CLIENT_TOOLS_RUN_CAPABILITY_ID,
        ClientToolsCapability,
        ClientToolsRunCapability,
    )
    from a13n_harness.tools.invocation import (
        TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID,
        ToolExecutionBoundaryCapability,
    )
    from a13n_harness.tools.policy import (
        INVOCATION_POLICY_CAPABILITY_ID,
        InvocationPolicyCapability,
    )

    capability_types = (
        ToolExecutionBoundaryCapability,
        MessageIntegrityFilterCapability,
        LifecycleEventCapability,
        ModelContextCoordinatorCapability,
        InvocationPolicyCapability,
        AbstractModelCostCapability,
        ClientToolsCapability,
        ClientToolsRunCapability,
        CodeActCapability,
        DynamicEnvironmentCapability,
        ShellReviewCapability,
        DelegationCapability,
        DelegationRunCapability,
    )
    names = frozenset(
        {
            *(capability_type.__name__ for capability_type in capability_types),
            TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID,
            MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID,
            LIFECYCLE_EVENT_CAPABILITY_ID,
            MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID,
            INVOCATION_POLICY_CAPABILITY_ID,
            MODEL_COST_CAPABILITY_ID,
            CLIENT_TOOLS_CAPABILITY_ID,
            CLIENT_TOOLS_RUN_CAPABILITY_ID,
            CODEACT_CAPABILITY_ID,
            DYNAMIC_ENVIRONMENT_CAPABILITY_ID,
            SHELL_REVIEW_CAPABILITY_ID,
            DELEGATION_CAPABILITY_ID,
            DELEGATION_RUN_CAPABILITY_ID,
        }
    )
    return capability_types, names


def _is_reserved_harness_capability_type(capability_type: type[AbstractCapability[AgentContext]]) -> bool:
    reserved_types, _ = _reserved_harness_capability_contract()
    return issubclass(capability_type, reserved_types)


@dataclass(frozen=True, slots=True)
class CapabilityTypeRegistration:
    """One Host-authorized declarative Capability type and its stable wire name."""

    serialization_name: str
    capability_type: type[AbstractCapability[AgentContext]]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.serialization_name, str)
            or not self.serialization_name
            or self.serialization_name != self.serialization_name.strip()
        ):
            raise DefinitionError(
                "Capability serialization_name must be a non-blank string without surrounding whitespace.",
                code="capability_type_name_invalid",
            )
        capability_type = self.capability_type
        if not isinstance(capability_type, type) or not issubclass(capability_type, AbstractCapability):
            raise DefinitionError(
                "Registered Capability types must inherit AbstractCapability.",
                code="capability_type_invalid",
            )
        if "__dataclass_fields__" not in capability_type.__dict__:
            raise DefinitionError(
                "Registered Capability types must be directly decorated with @dataclass.",
                code="capability_type_invalid",
                details={"capability_type": capability_type.__name__},
            )
        if _is_reserved_harness_capability_type(capability_type):
            raise DefinitionError(
                "Reserved Harness Capability types cannot enter the declarative catalog.",
                code="capability_type_scope_invalid",
                details={"capability_type": capability_type.__name__},
            )
        _, reserved_names = _reserved_harness_capability_contract()
        if self.serialization_name in reserved_names:
            raise DefinitionError(
                "Custom Capability serialization names must not collide with Harness contracts.",
                code="capability_type_catalog_invalid",
                details={"serialization_name": self.serialization_name},
            )
        native_name = capability_type.get_serialization_name()
        if native_name != self.serialization_name:
            raise DefinitionError(
                "Capability registration name must match get_serialization_name().",
                code="capability_type_name_mismatch",
                details={
                    "capability_type": capability_type.__name__,
                    "serialization_name": self.serialization_name,
                },
            )


@dataclass(frozen=True, slots=True)
class CapabilityTypeCatalog(Mapping[str, CapabilityTypeRegistration]):
    """Process-local immutable set of exact declarative Capability type grants."""

    registrations: tuple[CapabilityTypeRegistration, ...] = ()
    _items: Mapping[str, CapabilityTypeRegistration] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        registrations = tuple(self.registrations)
        if not all(isinstance(item, CapabilityTypeRegistration) for item in registrations):
            raise DefinitionError(
                "Capability type catalog entries must be CapabilityTypeRegistration values.",
                code="capability_type_registration_invalid",
            )
        items: dict[str, CapabilityTypeRegistration] = {}
        seen_types: set[type[AbstractCapability[AgentContext]]] = set()
        for registration in registrations:
            if registration.serialization_name in items or registration.capability_type in seen_types:
                raise DefinitionError(
                    "Capability type catalog registrations must have unique names and types.",
                    code="capability_type_duplicate",
                    details={"serialization_name": registration.serialization_name},
                )
            items[registration.serialization_name] = registration
            seen_types.add(registration.capability_type)

        for registration in registrations:
            if registration.serialization_name in CAPABILITY_TYPES:
                raise DefinitionError(
                    "Custom Capability serialization names must not collide with native types.",
                    code="capability_type_catalog_invalid",
                    details={"serialization_name": registration.serialization_name},
                )

        custom_types = tuple(item.capability_type for item in registrations)
        try:
            # Native schema construction validates collisions with built-ins and confirms
            # that each exact type can participate in deterministic AgentSpec decoding.
            AgentSpec.model_json_schema_with_capabilities(custom_types)
        except Exception as exc:
            raise DefinitionError(
                "Capability type catalog is incompatible with native AgentSpec reconstruction.",
                code="capability_type_catalog_invalid",
            ) from exc

        object.__setattr__(self, "registrations", registrations)
        object.__setattr__(self, "_items", MappingProxyType(items))

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __getitem__(self, name: str) -> CapabilityTypeRegistration:
        return self._items[name]

    @property
    def custom_capability_types(self) -> tuple[type[AbstractCapability[AgentContext]], ...]:
        """Return the exact native classes authorized for this builder."""
        return tuple(item.capability_type for item in self.registrations)

    @classmethod
    def from_types(
        cls,
        capability_types: Sequence[type[AbstractCapability[AgentContext]]],
    ) -> CapabilityTypeCatalog:
        """Construct registrations from each type's required native serialization name."""
        registrations: list[CapabilityTypeRegistration] = []
        for capability_type in capability_types:
            if not isinstance(capability_type, type) or not issubclass(capability_type, AbstractCapability):
                raise DefinitionError(
                    "Registered Capability types must inherit AbstractCapability.",
                    code="capability_type_invalid",
                )
            serialization_name = capability_type.get_serialization_name()
            if serialization_name is None:
                raise DefinitionError(
                    "Registered Capability types must define a serialization name.",
                    code="capability_type_name_invalid",
                    details={"capability_type": capability_type.__name__},
                )
            registrations.append(
                CapabilityTypeRegistration(
                    serialization_name=serialization_name,
                    capability_type=capability_type,
                )
            )
        return cls(tuple(registrations))
