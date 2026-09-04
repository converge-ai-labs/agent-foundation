"""Runtime selection signatures for lock reuse decisions."""

from __future__ import annotations

from ..domain import (
    ResolvedPluginVersion,
    ResolvedSubagentEdge,
)
from .contracts import (
    PreparedAgentInvocation,
)


def runtime_selection_unchanged(
    prepared: PreparedAgentInvocation,
    resolved_plugins: tuple[ResolvedPluginVersion, ...],
    resolved_subagents: tuple[ResolvedSubagentEdge, ...],
) -> bool:
    return plugin_runtime_signature(resolved_plugins) == plugin_runtime_signature(
        prepared.revision.resolved_plugin_versions
    ) and subagent_runtime_signature(resolved_subagents) == subagent_runtime_signature(
        prepared.revision.resolved_subagents
    )


def plugin_runtime_signature(plugins: tuple[ResolvedPluginVersion, ...]) -> frozenset[tuple[str, ...]]:
    return frozenset(
        (
            item.plugin_id,
            item.plugin_version_id,
            item.plugin_key,
            item.distribution_name,
            item.version,
            item.top_level_package,
            item.wheel_digest,
        )
        for item in plugins
    )


def subagent_runtime_signature(subagents: tuple[ResolvedSubagentEdge, ...]) -> frozenset[str]:
    return frozenset(item.child_agent_revision_id for item in subagents)
