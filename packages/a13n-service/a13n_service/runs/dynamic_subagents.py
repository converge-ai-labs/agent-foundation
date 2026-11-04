"""Task-specific definitions derived once from an accepted parent's capabilities."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated

from a13n_harness import AgentContext
from a13n_harness.builder import DelegationContextPolicy
from a13n_harness.capabilities import AsyncExecutionView
from a13n_harness.tools.identity import source_tool_prefix
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from a13n_harness.toolsets.delegation import project_delegation_input
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import invalid
from a13n_service.infra.platform import PLATFORM_GROUPS
from a13n_service.resources.agents.schemas import AgentConfig, Instructions
from a13n_service.resources.agents.validation import validate_config
from a13n_service.resources.models.service import model_settings, resolve_model
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

SelectionKeys = Annotated[tuple[str, ...], Field(max_length=512)]


class CapabilitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    toolsets: SelectionKeys | None = None
    connections: SelectionKeys | None = None
    skills: SelectionKeys | None = None
    builtin_skills: SelectionKeys | None = None
    client_tools: SelectionKeys | None = None
    plugins: SelectionKeys | None = None


class Spawn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=63)
    instructions: Instructions
    prompt: str = Field(min_length=1, max_length=1024 * 1024)
    model: str | None = None
    thinking: str | bool | None = None
    selection: CapabilitySelection | None = None


def _select[K: str, T](available: Mapping[K, T], selected: tuple[str, ...] | None, field: str) -> dict[K, T]:
    if selected is None:
        return dict(available)
    if len(set(selected)) != len(selected) or set(selected) - available.keys():
        raise invalid(f"selection.{field}", "Select distinct capabilities available to the parent")
    return {key: value for key, value in available.items() if key in selected}


async def derive(
    session: AsyncSession,
    runtime: Runtime,
    principal: Principal,
    scope: WorkspaceScope,
    authority: ExecutionAuthority,
    parent: AgentConfig,
    request: Spawn,
) -> AgentConfig:
    """Freeze a normalized leaf; all resource selections are exact parent subsets."""
    policy = parent.subagents.dynamic
    if not policy.enabled:
        raise invalid("subagents.dynamic", "Dynamic spawning is disabled")
    model = request.model or policy.default_model or parent.model
    if model not in {parent.model, *policy.allowed_models}:
        raise invalid("model", "The model is not allowed by the parent's dynamic policy")
    target = await resolve_model(session, principal, scope, model, authority=authority)
    same_model = model == parent.model
    settings = dict(parent.model_settings) if same_model else {}
    thinking = request.thinking if request.thinking is not None else policy.default_thinking
    if thinking is None and same_model:
        thinking = settings.get("thinking", target.config.defaults().get("thinking"))
    if thinking is None:
        thinking = target.config.defaults().get("thinking")
    if thinking is not None:
        settings["thinking"] = thinking
    model_settings(target.config, target.provider, settings, registry=runtime.registry, field="thinking")
    selection = request.selection or CapabilitySelection()
    selected_toolsets = _select(
        {key: value for key, value in parent.toolsets.items() if value.enabled}, selection.toolsets, "toolsets"
    )
    toolsets = {
        key: value if key in selected_toolsets else value.model_copy(update={"enabled": False})
        for key, value in parent.toolsets.items()
    }
    connections = _select(
        {item.connection_id: item for item in parent.connection_tools}, selection.connections, "connections"
    )
    skills = _select({item.skill_id: item for item in parent.skills}, selection.skills, "skills")
    builtins = _select(dict.fromkeys(parent.builtin_skills), selection.builtin_skills, "builtin_skills")
    client_tools = _select({item.name: item for item in parent.client_tools}, selection.client_tools, "client_tools")
    plugins = _select({item.instance_name: item for item in parent.plugins}, selection.plugins, "plugins")
    search = parent.tool_search
    if search is not None:
        search = search.model_copy(
            update={
                "groups": {
                    name: group.model_copy(
                        update={
                            "capabilities": tuple(key for key in group.capabilities if key in selected_toolsets),
                            "connections": tuple(key for key in group.connections if key in connections),
                            "plugins": tuple(key for key in group.plugins if key in plugins),
                            "platform": group.platform and bool(selected_toolsets.keys() & set(PLATFORM_GROUPS)),
                        }
                    )
                    for name, group in search.groups.items()
                }
            }
        )
    reviewer = parent.reviewer
    if reviewer is not None:
        removed = tuple(
            source_tool_prefix(item.connection_id, kind="mcp")
            for item in parent.connection_tools
            if item.connection_id not in connections
        )
        reviewer = reviewer.model_copy(
            update={"rules": {key: value for key, value in reviewer.rules.items() if not key.startswith(removed)}}
        )
    instructions = "\n\n".join(part for part in (parent.instructions, request.instructions) if part)
    if len(instructions) > 256 * 1024:
        raise invalid("instructions", "Combined parent and task instructions exceed 262144 characters")
    config = AgentConfig.model_validate(
        {
            **dict(parent),
            "model": model,
            "model_settings": settings,
            "model_characteristics": parent.model_characteristics if same_model else {},
            "instructions": instructions,
            "toolsets": toolsets,
            "connection_tools": tuple(connections.values()),
            "skills": tuple(skills.values()),
            "builtin_skills": frozenset(builtins),
            "client_tools": tuple(client_tools.values()),
            "plugins": tuple(plugins.values()),
            "tool_search": search,
            "reviewer": reviewer,
            "subagents": {},
            "output_spec": {},
            "background_compaction": {"enabled": False},
            "default_environment_template_id": None,
            "memory_mounts": (),
            "memory": {},
        }
    )
    return await validate_config(
        session,
        principal,
        scope,
        None,
        config,
        registry=runtime.registry,
        plugins=runtime.plugins,
        verb="run",
        authority=authority,
        held=parent,
    )


class SpawnCapability(AbstractCapability[AgentContext]):
    id = "a13n.service.dynamic-subagents"

    def __init__(self, spawn: Callable[[Spawn, str | None], Awaitable[AsyncExecutionView]], config: AgentConfig):
        self.spawn, self.config = spawn, config

    def get_instructions(self) -> str:
        selected = self.config
        inventory = {
            "toolsets": [key for key, value in selected.toolsets.items() if value.enabled],
            "connections": [item.connection_id for item in selected.connection_tools],
            "skills": [item.skill_id for item in selected.skills],
            "builtin_skills": sorted(selected.builtin_skills),
            "client_tools": [item.name for item in selected.client_tools],
            "plugins": [item.instance_name for item in selected.plugins],
        }
        return (
            "spawn_subagent starts an asynchronous task-specific leaf agent sharing your environments. "
            "Its instructions extend your authored instructions. Omit selection categories to inherit them, "
            "or pass a subset (empty lists remove a category). No conversation history is copied. "
            f"Available selection identifiers: {inventory}. "
            f"Allowed models: {sorted({selected.model, *selected.subagents.dynamic.allowed_models})}."
        )

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.spawn_subagent,
                    description="Start a task-specific asynchronous subagent; returns an execution ID for the standard subagent controls.",
                    harness_metadata=HarnessToolMetadata(
                        tool_id="subagents.spawn",
                        effects=frozenset({"write"}),
                        credential_audiences=(),
                        idempotency="provider_key",
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                        ),
                    ),
                )
            ],
            id="a13n-service-dynamic-subagents",
        )

    async def spawn_subagent(
        self,
        ctx: RunContext[AgentContext],
        name: Annotated[str, Field(min_length=1, max_length=63)],
        instructions: Instructions,
        prompt: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
        model: str | None = None,
        thinking: str | bool | None = None,
        selection: CapabilitySelection | None = None,
    ) -> JsonValue:
        projected = project_delegation_input(ctx, DelegationContextPolicy(), prompt)
        if len(projected) > 1024 * 1024:
            raise ToolFailed("The delegated input exceeds the 1 MiB character limit after context projection")
        result = await self.spawn(
            Spawn(
                name=name,
                instructions=instructions,
                prompt=projected,
                model=model,
                thinking=thinking,
                selection=selection,
            ),
            ctx.tool_call_id,
        )
        return result.model_dump(mode="json")
