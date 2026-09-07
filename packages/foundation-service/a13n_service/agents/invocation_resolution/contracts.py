"""Typed contracts for two-phase Agent invocation resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from a13n_service.connectivity.selection_domain import (
    ConnectorConnectionRunSelection,
    MCPConnectionRunSelection,
)
from a13n_service.connectivity.selection_resolution import (
    PreparedConnectivity,
)
from a13n_service.iam import (
    AuthenticatedActor,
)
from a13n_service.models.runtime import PreparedModelExecution

from ..domain import (
    AgentRevision,
    EffectiveAgentConfig,
    ResolvedPluginVersion,
    ResolvedSubagentEdge,
)
from ..invocation import MergedAgentRunConfig
from ..plugin_resolution import PreparedPluginSelections
from ..skill_resolution import (
    PreparedSkillLock,
)


class AgentSelectorKind(StrEnum):
    current = "current"
    exact = "exact"


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
    agent_revision_id: str
    selector_kind: AgentSelectorKind
    expected_current_revision_id: str | None
    revision_content_digest: str
    revision: AgentRevision
    merged: MergedAgentRunConfig
    model: PreparedModelExecution
    plugins: PreparedPluginSelections
    skills: tuple[PreparedSkillLock, ...]
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    subagents: tuple[PreparedChildInvocation, ...]
    connectivity: PreparedConnectivity


@dataclass(frozen=True, slots=True)
class FrozenAgentInvocation:
    agent_id: str
    agent_revision_id: str
    selector_kind: AgentSelectorKind
    effective_config: EffectiveAgentConfig
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...]
    mcp_connection_selections: tuple[MCPConnectionRunSelection, ...]
