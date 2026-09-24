"""The checks a configuration passes before a revision or a run freezes it, and the subagent graph it pins.

`validate_config` is the one entry point: it pins every skill and subagent edge to an exact revision and
reports the first failure as `invalid_argument` with the field's path relative to the configuration.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from a13n_harness import HarnessError
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue, ValidationError
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import at_field, conflict, invalid, not_found
from a13n_service.providers.registry import Registry, WebOperation, web_operations
from a13n_service.resources.agents import definition, toolsets
from a13n_service.resources.agents.schemas import AgentConfig, PluginSelection, SkillSelection, SubagentSelection
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.agents.toolsets import ScrapeConfiguration, SearchConfiguration
from a13n_service.resources.connections.schemas import ConnectionSelection
from a13n_service.resources.connections.service import validate_selection
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.environment_templates.service import resolve_template
from a13n_service.resources.memories.service import resolve_memory
from a13n_service.resources.models.service import resolve_media_model, resolve_model
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import WebProviderRow
from a13n_service.resources.skills.pins import require_pins
from a13n_service.resources.skills.schemas import SkillPin
from a13n_service.resources.skills.tables import SkillRow
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope

MAX_SUBAGENT_DEPTH = 16
MAX_SUBAGENT_NODES = 256


async def validate_config(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    agent_id: str | None,
    config: AgentConfig,
    *,
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
    verb: Verb = "read",
    authority: ExecutionAuthority | None = None,
    held: AgentConfig | None = None,
) -> AgentConfig:
    """`config` of agent `agent_id`, None for an agent not created yet, with its plugins normalized and its edges
    pinned, once every rule holds.

    Referenced resources must be usable by `actor` with `verb`: an author reads them, a run's principal runs
    them under the run's authority. `held` is the revision a run's override changes: a pin it already holds is
    accepted without an archival check, as plain runs of that revision accept it.
    """
    _check_rules(config)
    definition.output_type(config.output_spec)
    config = config.model_copy(
        update={
            "plugins": _normalized_plugins(config, plugins),
            "skills": await _pin_skills(
                session, scope.workspace_id, config.skills, () if held is None else held.skills
            ),
            "subagents": await _pin_subagents(
                session, scope.workspace_id, config.subagents, {} if held is None else held.subagents
            ),
        }
    )
    with at_field("model.model_id"):
        model = await resolve_model(session, actor, scope, config.model.model_id, verb=verb, authority=authority)
    registry.check_model_settings(model.config.model_api, config.model.settings, field="model.settings")
    if config.reviewer is not None:
        with at_field("reviewer.model"):
            reviewer = await resolve_model(session, actor, scope, config.reviewer.model, verb=verb, authority=authority)
        if config.reviewer.model_settings is not None:
            registry.check_model_settings(
                reviewer.config.model_api, config.reviewer.model_settings, field="reviewer.model_settings"
            )
    for kind, model_id in config.media_understanding.selections().items():
        with at_field(f"media_understanding.{kind}"):
            await resolve_media_model(session, actor, scope, kind, model_id, verb=verb, authority=authority)
    types: dict[str, str] = {}
    for index, selection in enumerate(config.connection_tools):
        with at_field(f"connection_tools.{index}"):
            types[selection.connection_id] = await validate_selection(session, actor, scope, selection)
    try:
        definition.tool_permissions(config, types)
    except ValidationError:
        raise invalid("connection_tools", "declares more tool permissions than an agent may have") from None
    await _check_web(session, actor, scope, config, registry=registry, verb=verb, authority=authority)
    templates = {"default_environment_template_id": config.default_environment_template_id} | {
        f"subagents.{name}.environment.template_id": edge.environment.template_id
        for name, edge in config.subagents.items()
    }
    for path, template_id in templates.items():
        if template_id is not None:
            with at_field(path):
                await resolve_template(session, actor, scope, template_id, verb=verb, authority=authority)
    if held is None:
        # A run's thread takes default mounts at its first acceptance, which checks them then.
        for index, mount in enumerate(config.memory_mounts):
            with at_field(f"memory_mounts.{index}.memory_id"):
                await resolve_memory(session, actor, scope, mount.memory_id, verb=verb, authority=authority)
    await subagent_graph(session, scope.workspace_id, agent_id, config)
    return config


def _check_rules(config: AgentConfig) -> None:
    """The rules a configuration obeys on its own, before any referenced resource is read."""
    for operation, tool in _provider_tools(config).items():
        if tool.provider_id is None:
            raise invalid(f"toolsets.web.tools.{operation}.config.provider_id", "required while the tool is enabled")
    reserved = toolsets.model_names(config.toolsets) | ({definition.QUESTION_TOOL} if config.user_questions else set())
    for index, tool in enumerate(config.client_tools):
        if tool.name in reserved:
            raise invalid(f"client_tools.{index}.name", "is the name of a built-in tool")
        if error := _schema_error(tool.parameters_json_schema):
            raise invalid(f"client_tools.{index}.parameters_json_schema", error)
    if config.reviewer is None and "review" in definition.declared_permissions(config):
        raise invalid("reviewer", "required by the review permission")
    if config.subagent_mode == "async":
        # A child run's usage bound counts model requests only; the other limits bound inline delegation.
        for name, edge in config.subagents.items():
            limits = edge.usage_limits
            if limits is not None and limits != UsageLimits(request_limit=limits.request_limit):
                raise invalid(f"subagents.{name}.usage_limits", "an async child run takes only request_limit")


def _provider_tools(config: AgentConfig) -> dict[WebOperation, SearchConfiguration | ScrapeConfiguration]:
    """The enabled web tools that run through a web provider, by operation."""
    web = toolsets.web_tools(config.toolsets)
    if web is None:
        return {}
    tools: tuple[tuple[WebOperation, SearchConfiguration | ScrapeConfiguration | None], ...] = (
        ("search", web.search),
        ("scrape", web.scrape),
    )
    return {operation: tool for operation, tool in tools if tool is not None}


def _schema_error(schema: Mapping[str, JsonValue]) -> str | None:
    """Why a client tool's parameter schema is unusable: clients receive it without its references' targets."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return "not a valid JSON Schema"

    def external(value: JsonValue) -> bool:
        if isinstance(value, dict):
            reference = value.get("$ref")
            return (isinstance(reference, str) and not reference.startswith("#")) or any(map(external, value.values()))
        return isinstance(value, list) and any(map(external, value))

    return "references a schema outside itself" if external(dict(schema)) else None


def _normalized_plugins(config: AgentConfig, plugins: HarnessPluginFactoryCatalog) -> tuple[PluginSelection, ...]:
    """Each plugin's config as its installed factory normalizes it."""
    normalized = []
    for index, selected in enumerate(config.plugins):
        try:
            values = plugins.validate_configuration(selected.plugin_key, selected.config)
        except HarnessError as error:
            raise invalid(f"plugins.{index}", error.code) from None
        normalized.append(selected.model_copy(update={"config": dict(values)}))
    return tuple(normalized)


async def _pin_skills(
    session: AsyncSession, workspace_id: str, skills: Sequence[SkillSelection], held: Collection[SkillSelection]
) -> tuple[SkillSelection, ...]:
    """Each skill at its selected revision, by default the skill's default revision; archived skills refuse a pin
    that is not `held` already."""
    unpinned = {skill.skill_id for skill in skills if skill.revision_id is None}
    defaults = dict(
        (
            await session.execute(
                select(SkillRow.id, SkillRow.default_revision_id).where(
                    SkillRow.workspace_id == workspace_id, SkillRow.id.in_(unpinned)
                )
            )
        )
        .tuples()
        .all()
    )
    pinned: list[SkillSelection] = []
    checked: dict[str, SkillPin] = {}
    for index, skill in enumerate(skills):
        revision_id = skill.revision_id or defaults.get(skill.skill_id)
        if revision_id is None:
            with at_field(f"skills.{index}"):
                raise not_found(SkillRow.KIND, skill.skill_id)
        selection = skill.model_copy(update={"revision_id": revision_id})
        if selection not in held:
            checked[f"skills.{index}"] = SkillPin(skill_id=skill.skill_id, revision_id=revision_id)
        pinned.append(selection)
    await require_pins(session, workspace_id, checked)
    return tuple(pinned)


async def _pin_subagents(
    session: AsyncSession,
    workspace_id: str,
    subagents: Mapping[str, SubagentSelection],
    held: Mapping[str, SubagentSelection],
) -> dict[str, SubagentSelection]:
    """Each edge at its selected revision, by default the agent's default revision; archived agents refuse an
    edge that is not `held` already."""
    heads = {
        head.id: head
        for head in await session.scalars(
            select(AgentRow).where(
                AgentRow.workspace_id == workspace_id, AgentRow.id.in_({edge.agent_id for edge in subagents.values()})
            )
        )
    }
    wanted = {
        revision_id
        for edge in subagents.values()
        if edge.agent_id in heads
        and (revision_id := edge.revision_id or heads[edge.agent_id].default_revision_id) is not None
    }
    revisions = set(
        (
            await session.execute(
                select(AgentRevisionRow.agent_id, AgentRevisionRow.id).where(
                    AgentRevisionRow.workspace_id == workspace_id, AgentRevisionRow.id.in_(wanted)
                )
            )
        ).tuples()
    )
    pinned = {}
    for name, edge in subagents.items():
        with at_field(f"subagents.{name}"):
            head = heads.get(edge.agent_id)
            if head is None:
                raise not_found(AgentRow.KIND, edge.agent_id)
            selection = edge.model_copy(update={"revision_id": edge.revision_id or head.default_revision_id})
            if head.archived_at is not None and _pin(selection) not in {_pin(kept) for kept in held.values()}:
                raise conflict(head.KIND, head.id, "archived")
            if (head.id, selection.revision_id) not in revisions:
                raise not_found(AgentRevisionRow.KIND, selection.revision_id or head.id)
        pinned[name] = selection
    return pinned


def _pin(edge: SubagentSelection) -> tuple[str, str | None]:
    return edge.agent_id, edge.revision_id


async def connection_types(
    session: AsyncSession, workspace_id: str, selections: Sequence[ConnectionSelection]
) -> dict[str, str]:
    """The type of each selected connection, which decides how the Harness identifies its tools."""
    selected = [selection.connection_id for selection in selections]
    types = dict(
        (
            await session.execute(
                select(ConnectionRow.id, ConnectionRow.type).where(
                    ConnectionRow.workspace_id == workspace_id, ConnectionRow.id.in_(selected)
                )
            )
        )
        .tuples()
        .all()
    )
    for connection_id in selected:
        if connection_id not in types:
            raise not_found(ConnectionRow.KIND, connection_id)
    return types


async def _check_web(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    config: AgentConfig,
    *,
    registry: Registry,
    verb: Verb,
    authority: ExecutionAuthority | None,
) -> None:
    """Each enabled search and scrape names a usable web provider whose type serves the operation."""
    for operation, tool in _provider_tools(config).items():
        assert tool.provider_id is not None
        with at_field(f"toolsets.web.tools.{operation}.config.provider_id"):
            provider = await resolve_provider(
                session, actor, WebProviderRow, scope, tool.provider_id, verb=verb, authority=authority
            )
            served = registry.get("web", provider.type)
            if operation not in web_operations(served):
                raise invalid("web_provider", f"{provider.type} does not support {operation}")
            if tool.restricted and operation == "scrape" and not served.supports_restricted_scrape:
                raise invalid("web_provider", f"{provider.type} cannot restrict scrape domains")


@dataclass(frozen=True, slots=True)
class Subagent:
    """A pinned subagent revision, and what its parents show the model about it by default."""

    agent_id: str
    revision_id: str
    config: AgentConfig
    # The child agent's description, else its name.
    description: str


async def subagent_graph(
    session: AsyncSession, workspace_id: str, agent_id: str | None, config: AgentConfig
) -> dict[str, Subagent]:
    """Every revision the pinned edges of agent `agent_id`'s `config` reach, by revision ID; no path can lead
    back to an agent not created yet (None).

    Edges of an inline agent are followed further: their revisions run inside the same run. Edges of an async
    agent start child runs, which load their own graph. An inline path may not return to the root's agent, nest
    deeper than `MAX_SUBAGENT_DEPTH` or reach more than `MAX_SUBAGENT_NODES` revisions.
    """
    graph: dict[str, Subagent] = {}
    # The inline agents at one depth, each with the root edge its path leaves through; a revision reached
    # through several paths is followed once per depth.
    level: dict[str | None, tuple[AgentConfig, str | None]] = {None: (config, None)}
    depth = 0
    while level:
        wanted = {edge.revision_id for node, _ in level.values() for edge in node.subagents.values()} - graph.keys()
        if wanted:
            rows = await session.execute(
                select(AgentRevisionRow, AgentRow)
                .join(AgentRow, AgentRow.id == AgentRevisionRow.agent_id)
                .where(AgentRevisionRow.workspace_id == workspace_id, AgentRevisionRow.id.in_(wanted))
            )
            for revision, head in rows.tuples():
                graph[revision.id] = Subagent(
                    agent_id=revision.agent_id,
                    revision_id=revision.id,
                    config=AgentConfig.model_validate(revision.config),
                    description=head.description or head.name,
                )
        depth += 1
        following: dict[str | None, tuple[AgentConfig, str | None]] = {}
        for node, via in level.values():
            for name, edge in node.subagents.items():
                path = via or f"subagents.{name}"
                child = graph.get(edge.revision_id or "")
                if child is None or child.agent_id != edge.agent_id:
                    raise invalid(path, "a pinned subagent revision is missing")
                if node.subagent_mode != "inline":
                    continue
                if child.agent_id == agent_id:
                    raise invalid(path, "the subagents lead back to this agent")
                if depth > MAX_SUBAGENT_DEPTH or len(graph) > MAX_SUBAGENT_NODES:
                    raise invalid(path, "the subagent graph is too deep or too large")
                following.setdefault(child.revision_id, (child.config, path))
        level = following
    return graph
