"""Typed contracts for two-phase Agent invocation resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from a13n_service.agent_configuration.context import ConfigurationRunContext
from a13n_service.connectivity.selection_domain import (
    ConnectionRunSelection,
)
from a13n_service.connectivity.selection_resolution import (
    PreparedConnectivity,
)
from a13n_service.iam import (
    AuthenticatedActor,
)
from a13n_service.models.runtime import PreparedModelExecution

from ..domain import (
    EffectiveAgentConfig,
    ResolvedSubagentEdge,
)
from ..invocation import MergedAgentRunConfig
from ..skill_resolution import (
    PreparedSkillLock,
)


class AgentSelectorKind(StrEnum):
    current = "current"
    exact = "exact"
    configuration = "configuration"


class RootAgentStatePolicy(StrEnum):
    invocable = "invocable"
    disabled_allowed = "disabled_allowed"
    archived_allowed = "archived_allowed"


@dataclass(frozen=True, slots=True)
class PreparedChildInvocation:
    edge: ResolvedSubagentEdge
    invocation: PreparedAgentInvocation


@dataclass(frozen=True, slots=True)
class PreparedAgentInvocation:
    root_state_policy: RootAgentStatePolicy
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_id: str
    agent_revision_id: str | None
    selector_kind: AgentSelectorKind
    expected_default_revision_id: str | None
    revision_content_digest: str | None
    merged: MergedAgentRunConfig
    model: PreparedModelExecution
    skills: tuple[PreparedSkillLock, ...]
    subagents: tuple[PreparedChildInvocation, ...]
    connectivity: PreparedConnectivity
    reviewer_model: PreparedModelExecution | None = None
    configuration_context: ConfigurationRunContext | None = None

    def __post_init__(self) -> None:
        protected = self.configuration_context is not None
        if protected != (self.selector_kind is AgentSelectorKind.configuration):
            raise ValueError("Only protected configuration invocation can omit an AgentRevision")
        if (protected and (self.agent_revision_id is not None or self.revision_content_digest is not None)) or (
            not protected and (self.agent_revision_id is None or self.revision_content_digest is None)
        ):
            raise ValueError("Invocation source and Revision identity disagree")


@dataclass(frozen=True, slots=True)
class FrozenAgentInvocation:
    agent_id: str
    agent_revision_id: str | None
    selector_kind: AgentSelectorKind
    effective_config: EffectiveAgentConfig
    connection_selections: tuple[ConnectionRunSelection, ...]
