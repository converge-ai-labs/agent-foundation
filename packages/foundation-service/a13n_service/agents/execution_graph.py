"""The accepted descendants that execute inside their root Attempt."""

from .domain import ChildAgentExecution, EffectiveAgentConfig, ResolvedSubagentEdge
from .resolution import MAX_SUBAGENT_NODES


def inline_child_executions(
    config: EffectiveAgentConfig,
) -> dict[str, tuple[ResolvedSubagentEdge, ChildAgentExecution]]:
    if config.subagent_mode == "async":
        return {}
    children: dict[str, tuple[ResolvedSubagentEdge, ChildAgentExecution]] = {}
    pending = [config]
    while pending:
        selected = pending.pop()
        for edge in selected.resolved_subagents:
            child = selected.child_configs[edge.child_agent_revision_id]
            existing = children.get(edge.child_agent_revision_id)
            if existing is not None:
                if existing[0].child_agent_id != edge.child_agent_id or existing[1] != child:
                    raise ValueError("Accepted child execution snapshots disagree")
                continue
            if len(children) >= MAX_SUBAGENT_NODES:
                raise ValueError("Accepted Agent graph exceeds the reconstruction bound")
            children[edge.child_agent_revision_id] = (edge, child)
            pending.append(child.effective_config)
    return children
