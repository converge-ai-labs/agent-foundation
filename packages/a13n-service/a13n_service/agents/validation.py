"""Authoritative validation for Revision creation and Agent invocation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.tools.client import ClientToolDefinition
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .domain import OutputSpec, ProtocolConfig

_DEFAULT_OUTPUT_MODES = frozenset({"text"})
_DEFAULT_EVENT_VISIBILITY = frozenset(
    {
        "a13n.service.artifact",
        "a13n.service.replay_gap",
        "a13n.service.run_status",
        "a13n.service.run_recovery",
    }
)


@dataclass(frozen=True, slots=True)
class AgentProtocolPolicy:
    """Finite deployment policy applied only by Create Revision and Run acceptance."""

    output_modes: frozenset[str] = _DEFAULT_OUTPUT_MODES
    event_visibility: frozenset[str] = _DEFAULT_EVENT_VISIBILITY
    max_input_bytes: int = 64 * 1024 * 1024
    max_output_bytes: int = 256 * 1024 * 1024
    max_event_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.output_modes:
            raise ValueError("output_modes must contain at least one supported mode")
        if any(not value for value in self.output_modes | self.event_visibility):
            raise ValueError("protocol registries must contain bounded non-empty names")
        if min(self.max_input_bytes, self.max_output_bytes, self.max_event_bytes) < 1:
            raise ValueError("protocol hard ceilings must be positive")


class AgentConfigValidationError(Exception):
    """One bounded semantic failure in a complete effective Agent configuration."""

    def __init__(self, reason: str, *, path: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


class AgentConfigValidationInput(Protocol):
    output_spec: OutputSpec | None
    protocol: ProtocolConfig
    client_tools: tuple[ClientToolDefinition, ...]


def validate_agent_config(
    config: AgentConfigValidationInput,
    *,
    protocol_policy: AgentProtocolPolicy,
) -> None:
    """Validate the complete final configuration without resolving managed resources."""

    _validate_json_schemas(config)
    _validate_protocol(config, protocol_policy)


def _validate_protocol(config: AgentConfigValidationInput, policy: AgentProtocolPolicy) -> None:
    protocol = config.protocol
    if not protocol.output_modes:
        raise AgentConfigValidationError("protocol_output_modes_empty", path="protocol.output_modes")
    for index, mode in enumerate(protocol.output_modes):
        if mode not in policy.output_modes:
            raise AgentConfigValidationError(
                "protocol_output_mode_unsupported",
                path=f"protocol.output_modes.{index}",
            )
    for index, name in enumerate(protocol.event_visibility):
        if name not in policy.event_visibility:
            raise AgentConfigValidationError(
                "protocol_event_unsupported",
                path=f"protocol.event_visibility.{index}",
            )

    declared_client_tools = frozenset(item.name for item in config.client_tools)
    policy_names: set[str] = set()
    for index, item in enumerate(protocol.client_tools):
        if item.name in policy_names:
            raise AgentConfigValidationError(
                "protocol_client_tool_duplicate",
                path=f"protocol.client_tools.{index}.name",
            )
        policy_names.add(item.name)
        if item.name not in declared_client_tools:
            raise AgentConfigValidationError(
                "protocol_client_tool_unavailable",
                path=f"protocol.client_tools.{index}.name",
            )

    skill_ids: set[str] = set()
    for index, item in enumerate(protocol.a2a_skills):
        if item.id in skill_ids:
            raise AgentConfigValidationError(
                "protocol_a2a_skill_duplicate",
                path=f"protocol.a2a_skills.{index}.id",
            )
        skill_ids.add(item.id)

    limits = protocol.limits
    for name, value, ceiling in (
        ("max_input_bytes", limits.max_input_bytes, policy.max_input_bytes),
        ("max_output_bytes", limits.max_output_bytes, policy.max_output_bytes),
        ("max_event_bytes", limits.max_event_bytes, policy.max_event_bytes),
    ):
        if value > ceiling:
            raise AgentConfigValidationError(
                "protocol_limit_exceeds_deployment",
                path=f"protocol.limits.{name}",
            )


def _validate_json_schemas(config: AgentConfigValidationInput) -> None:
    schemas: list[tuple[str, Mapping[str, object], bool]] = []
    if config.output_spec is not None:
        if config.output_spec.schema_ is not None:
            schemas.append(("output_spec.schema", config.output_spec.schema_, False))
            schemas.extend(
                (f"output_spec.resources.{name}", schema, False)
                for name, schema in config.output_spec.resources.items()
            )
        for index, variant in enumerate(config.output_spec.variants or ()):
            schemas.append((f"output_spec.variants.{index}.schema", variant.schema_, False))
            schemas.extend(
                (f"output_spec.variants.{index}.resources.{name}", schema, False)
                for name, schema in variant.resources.items()
            )
    for name in ("input_data_schema", "state_schema", "context_schema"):
        schema = getattr(config.protocol, name)
        if schema is not None:
            schemas.append((f"protocol.{name}", schema, True))
    schemas.extend(
        (f"client_tools.{index}.parameters_json_schema", item.parameters_json_schema, True)
        for index, item in enumerate(config.client_tools)
    )
    for path, schema, self_contained in schemas:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise AgentConfigValidationError("json_schema_invalid", path=path) from error
        if self_contained and _contains_external_reference(schema):
            raise AgentConfigValidationError("json_schema_external_reference", path=path)


def _contains_external_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            return True
        return any(_contains_external_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_external_reference(item) for item in value)
    return False


__all__ = [
    "AgentConfigValidationError",
    "AgentProtocolPolicy",
    "validate_agent_config",
]
