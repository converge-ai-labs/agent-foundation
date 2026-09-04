"""Typed contracts for two-phase Agent invocation resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from a13n_service.agents.domain import AccountToolSelection
from a13n_service.connectivity.selection_domain import (
    ConnectorConnectionRunSelection,
    MCPConnectionRunSelection,
)
from a13n_service.connectivity.selection_resolution import (
    PreparedRevisionConnectivity,
    PreparedRunConnectivity,
)
from a13n_service.iam import (
    AuthenticatedActor,
)
from a13n_service.models.runtime import PreparedModelExecution

from ..domain import (
    AgentRevision,
    EffectiveAgentConfig,
    EnvironmentExecutionConfig,
    ResolvedPluginVersion,
    ResolvedSubagentEdge,
)
from ..environment_resolution import PreparedEnvironmentSelection
from ..invocation import AgentRunSensitiveValues, MergedAgentRun
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
class PreparedInvocationSubagent:
    edge: ResolvedSubagentEdge
    child_revision_digest: str
    child_runtime_lock_digest: str


PreparedInvocationModel = PreparedModelExecution


@dataclass(frozen=True, slots=True)
class PreparedAgentInvocation:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_id: str
    agent_revision_id: str
    selector_kind: AgentSelectorKind
    expected_current_revision_id: str | None
    revision_content_digest: str
    revision: AgentRevision
    merged: MergedAgentRun
    model: PreparedInvocationModel
    plugins: PreparedPluginSelections
    skills: tuple[PreparedSkillLock, ...]
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    environment: PreparedEnvironmentSelection | None
    resolved_environment: EnvironmentExecutionConfig | None
    subagents: tuple[PreparedInvocationSubagent, ...]
    connectivity: PreparedRevisionConnectivity | PreparedRunConnectivity | None


@dataclass(frozen=True, slots=True)
class FrozenAgentInvocation:
    agent_id: str
    agent_revision_id: str
    selector_kind: AgentSelectorKind
    effective_config: EffectiveAgentConfig
    sensitive_values: AgentRunSensitiveValues
    sensitive_values_digest: str
    account_selections: tuple[AccountToolSelection, ...]
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...]
    mcp_connection_selections: tuple[MCPConnectionRunSelection, ...]


@dataclass(frozen=True, slots=True)
class PreparedAgentRevisionGraph:
    invocations: tuple[PreparedAgentInvocation, ...]
    root_state_policy: RootAgentStatePolicy
