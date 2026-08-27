"""Trusted Agent identity values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from a13n_harness.errors import IdentityError


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise IdentityError(
            f"{field_name} must not be blank.",
            code="identity_invalid",
            details={"field": field_name},
        )
    return value


@dataclass(frozen=True, slots=True)
class AgentIdentityRef:
    """Stable workload principal supplied by trusted application code."""

    issuer: str
    subject: str

    def __post_init__(self) -> None:
        _require_non_blank(self.issuer, "issuer")
        _require_non_blank(self.subject, "subject")


@dataclass(frozen=True, slots=True)
class AgentInstanceRef:
    """Stable, non-authoritative reference to one logical Agent instance."""

    identity: AgentIdentityRef
    agent_instance_id: str

    def __post_init__(self) -> None:
        _require_non_blank(self.agent_instance_id, "agent_instance_id")


@dataclass(frozen=True, slots=True)
class AgentInstanceContext:
    """Fresh trusted identity and lineage binding for one Harness run."""

    identity: AgentIdentityRef
    agent_instance_id: str
    parent_agent_instance_id: str | None = None
    delegation_id: str | None = None
    actor: str | None = None
    host_refs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_blank(self.agent_instance_id, "agent_instance_id")
        optional_values = (
            ("parent_agent_instance_id", self.parent_agent_instance_id),
            ("delegation_id", self.delegation_id),
            ("actor", self.actor),
        )
        for field_name, value in optional_values:
            if value is not None:
                _require_non_blank(value, field_name)
        normalized_refs: dict[str, str] = {}
        for key, value in self.host_refs.items():
            normalized_refs[_require_non_blank(key, "host_refs key")] = _require_non_blank(value, "host_refs value")
        object.__setattr__(self, "host_refs", MappingProxyType(normalized_refs))

    def ref(self) -> AgentInstanceRef:
        """Return the stable instance reference without run-specific lineage."""
        return AgentInstanceRef(identity=self.identity, agent_instance_id=self.agent_instance_id)
