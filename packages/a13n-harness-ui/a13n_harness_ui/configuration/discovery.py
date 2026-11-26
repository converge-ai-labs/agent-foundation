"""Credential-free discovery of accepted resources for cross-Thread collaboration."""

from typing import Any, Literal

from a13n_harness_ui.errors import ConfigurationError

from .models import LoadedHarnessUiConfiguration, canonical_digest

ResourceKind = Literal["projects", "agents", "models"]


def resource_page(
    source: LoadedHarnessUiConfiguration,
    *,
    kind: ResourceKind,
    query: str | None,
    cursor: str | None,
    limit: int,
    allowed_agent_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Page small summaries, binding continuation to the accepted generation and query."""
    if not 1 <= limit <= 100 or (query is not None and len(query) > 512):
        raise ConfigurationError("Resource query is outside supported bounds.", code="resource_query_invalid")
    normalized = (query or "").strip().casefold()
    cursor_scope: list[Any] = [kind, normalized]
    if kind == "agents" and allowed_agent_ids is not None:
        cursor_scope.append(list(allowed_agent_ids))
    prefix = f"{source.source_digest}:{canonical_digest(cursor_scope)}:"
    offset = 0
    if cursor is not None:
        suffix = cursor.removeprefix(prefix)
        if not cursor.startswith(prefix) or not suffix.isascii() or not suffix.isdecimal() or len(suffix) > 9:
            raise ConfigurationError(
                "Resource cursor belongs to another generation or query; start a new listing.",
                code="resource_cursor_mismatch",
            )
        offset = int(suffix)
    resources = {"projects": source.projects, "agents": source.agents | source.acp_agents, "models": source.models}[
        kind
    ]
    if kind == "agents" and allowed_agent_ids is not None:
        allowed_set = set(allowed_agent_ids)
        resources = {resource_id: item for resource_id, item in resources.items() if resource_id in allowed_set}
    selected = sorted(
        (item for item in resources.values() if normalized in f"{item.id} {item.name}".casefold()),
        key=lambda item: item.id,
    )
    items: list[dict[str, Any]] = []
    for item in selected[offset : offset + limit]:
        summary: dict[str, Any] = {"id": item.id, "name": item.name}
        if kind == "agents":
            agent = source.agents.get(item.id)
            if agent is None:
                summary.update(kind="acp_agent", model_id=None)
            else:
                summary.update(model_id=agent.model, capability_ids=[entry.capability for entry in agent.capabilities])
        elif kind == "models":
            model = source.models[item.id]
            summary.update(route=model.route)
        else:
            project = source.projects[item.id]
            summary.update(root_count=len(project.roots), default_agent_id=project.defaults.agent)
        items.append(summary)
    next_offset = offset + len(items)
    return {
        "generation_digest": source.source_digest,
        kind: items,
        "total": len(selected),
        "next_cursor": f"{prefix}{next_offset}" if next_offset < len(selected) else None,
    }
