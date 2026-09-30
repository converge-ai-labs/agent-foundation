"""The Harness agent a run executes: its configuration resolved, its models opened, its definition built.

`resolve` reads what the definition depends on in the caller's short session, `open_models` opens the
models outside any session, and `build` composes the definition without I/O. A definition is built per
attempt, so its capabilities hold state for one Harness run only.

The definition selects every model by its model key, which `model_resolver` resolves to the opened model, so
each model call names the model it calls: the call check admits it, and its usage is attributed and priced, as
that model, whatever upstream model name it shares with another.

Everything bound to the attempt stays the worker's: boundaries, connections, environments, web backends,
skill content, asset publishing, the async subagent operator and the run bindings. `build` asks the
worker for them once per agent of the inline graph.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    DelegationContextPolicy,
    ExecutableAgent,
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessModelCharacteristics,
    ModelRecoveryPolicy,
    RunBindings,
    RunConfiguration,
    RunModelResolver,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    CompactionCapability,
    SubagentCapability,
    SubagentOperator,
    UserInteractionCapability,
)
from a13n_harness.capabilities.web import WebCapability
from a13n_harness.environment import DynamicEnvironmentCapability
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.models.inference import RequestHeadersModel
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog, HarnessPluginFactoryContext
from a13n_harness.token_pricing import TokenPricingCapability
from a13n_harness.tools.client import ClientToolsCapability, ClientToolsSpec
from a13n_harness.tools.permissions import ToolPermissionsCapability
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingError,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
    NativeInputMediaKind,
)
from a13n_logging import get_logger
from pydantic import JsonValue
from pydantic_ai.agent.abstract import AgentRetries
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model, ModelResolutionContext
from pydantic_ai.settings import ModelSettings
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError
from a13n_service.providers.model_settings import effective_settings
from a13n_service.providers.registry import Registry
from a13n_service.resources.agents import definition, toolsets
from a13n_service.resources.agents.schemas import AgentConfig, AgentOverride, SubagentSelection, apply_override
from a13n_service.resources.agents.service import SelectedRevision
from a13n_service.resources.agents.validation import connection_types, subagent_graph
from a13n_service.resources.models.media import workspace_media
from a13n_service.resources.models.runtime import open_model
from a13n_service.resources.models.service import ResolvedModel, model_settings, require_understanding, resolve_model
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedAgent:
    """One agent of the graph a run executes, with every live resource its definition reads."""

    agent_id: str
    revision_id: str
    # The revision's configuration; at the root, with the run's override applied.
    config: AgentConfig
    model: ResolvedModel
    reviewer: ResolvedModel | None
    media: Mapping[NativeInputMediaKind, ResolvedModel]
    # The type of each selected connection, which decides the identities of its tools.
    connection_types: Mapping[str, str]
    subagents: Mapping[str, "ResolvedSubagent"]

    def agents(self) -> Iterator["ResolvedAgent"]:
        """This agent and every agent of its inline graph, possibly repeated."""
        yield self
        for subagent in self.subagents.values():
            if subagent.agent is not None:
                yield from subagent.agent.agents()

    def models(self) -> Iterator[ResolvedModel]:
        """Every model this agent and its inline subagents call, possibly repeated."""
        for agent in self.agents():
            yield agent.model
            if agent.reviewer is not None:
                yield agent.reviewer
            yield from agent.media.values()


@dataclass(frozen=True, slots=True)
class ResolvedSubagent:
    selection: SubagentSelection
    # The child revision the stored edge pins.
    revision_id: str
    # What the parent's model is told about the child.
    description: str
    # The child resolved to run inside the parent's run; None when it runs as a child run of its own.
    agent: ResolvedAgent | None


async def resolve(
    session: AsyncSession,
    principal: Principal,
    scope: WorkspaceScope,
    revision: SelectedRevision,
    *,
    authority: ExecutionAuthority,
    override: AgentOverride | None,
    registry: Registry,
) -> ResolvedAgent:
    """The revision with the run's override applied, and every model and inline subagent it reaches.

    Models must still be usable by the run's principal under its authority; subagent revisions are pinned.
    """
    config = revision.config if override is None else apply_override(revision.config, override)
    graph = await subagent_graph(session, scope.workspace_id, revision.agent_id, config)
    models: dict[str, ResolvedModel] = {}

    async def model(key: str) -> ResolvedModel:
        if key not in models:
            models[key] = await resolve_model(session, principal, scope, key, authority=authority)
        return models[key]

    defaults = (await workspace_media(session, scope.workspace_id)).selections()

    async def media(config: AgentConfig) -> dict[NativeInputMediaKind, ResolvedModel]:
        """The agent's own selection, which must stay usable, over the workspace's defaults, which may not."""
        selected = config.media_understanding.selections()
        resolved = {}
        for kind, key in (defaults | selected).items():
            try:
                resolved[kind] = await model(key)
                require_understanding(resolved[kind], kind)
                selected_model = resolved[kind]
                model_settings(
                    selected_model.config,
                    selected_model.provider,
                    {},
                    registry=registry,
                    field=f"media_understanding.{kind}",
                )
            except ServiceError as error:
                if kind in selected:
                    raise
                # A default that stopped working must not fail every run of the workspace; the kind is unavailable.
                resolved.pop(kind, None)
                logger.warning(
                    "Workspace media default skipped",
                    extra={"workspace_id": scope.workspace_id, "kind": kind, "model": key, "code": error.code},
                )
        return resolved

    async def node(agent_id: str, revision_id: str, config: AgentConfig) -> ResolvedAgent:
        subagents = {}
        for name, edge in config.subagents.items():
            assert edge.revision_id is not None, "stored edges are pinned"
            child = graph[edge.revision_id]
            subagents[name] = ResolvedSubagent(
                selection=edge,
                revision_id=child.revision_id,
                description=edge.description or child.description,
                agent=await node(child.agent_id, child.revision_id, child.config)
                if config.subagent_mode == "inline"
                else None,
            )
        primary = await model(config.model)
        model_settings(
            primary.config, primary.provider, config.model_settings, registry=registry, field="model_settings"
        )
        reviewer = None if config.reviewer is None else await model(config.reviewer.model)
        if reviewer is not None and config.reviewer is not None:
            model_settings(
                reviewer.config,
                reviewer.provider,
                config.reviewer.model_settings or {},
                registry=registry,
                field="reviewer.model_settings",
            )
        return ResolvedAgent(
            agent_id=agent_id,
            revision_id=revision_id,
            config=config,
            model=primary,
            reviewer=reviewer,
            media=await media(config),
            connection_types=await connection_types(session, scope.workspace_id, config.connection_tools),
            subagents=subagents,
        )

    return await node(revision.agent_id, revision.revision_id, config)


async def open_models(
    stack: AsyncExitStack, runtime: Runtime, agent: ResolvedAgent, *, configuration: RunConfiguration | None = None
) -> dict[str, Model]:
    """Every model of the agent and its inline subagents, by model key, opened once and closed by `stack`."""
    opened: dict[str, Model] = {}
    for model in agent.models():
        if model.key not in opened:
            opened[model.key] = await stack.enter_async_context(
                open_model(
                    model,
                    registry=runtime.registry,
                    keys=runtime.keys,
                    policy=runtime.endpoint_policy.for_run(configuration or RunConfiguration()),
                    settings=runtime.settings.providers,
                )
            )
    return opened


def model_resolver(agent: ResolvedAgent, models: Mapping[str, Model]) -> RunModelResolver:
    """The run's resolution of the model keys the definition selects, to the models `open_models` opened."""

    selected = {model.key: model for model in agent.models()}

    async def resolve(context: ModelResolutionContext[AgentContext], model_id: str) -> Model:
        return _thread_model(selected[model_id], models[model_id], context.deps.thread_id)

    return resolve


type HostCapabilities = Callable[[ResolvedAgent], Sequence[AbstractCapability[AgentContext]]]
type ChildBindings = Callable[[ResolvedAgent, RunBindings], RunBindings]
type Operators = Callable[[ResolvedAgent], SubagentOperator]


def build(
    agent: ResolvedAgent,
    *,
    capabilities: HostCapabilities,
    child_bindings: ChildBindings | None = None,
    operators: Operators | None = None,
    plugins: HarnessPluginFactoryCatalog,
    instrumentation: HarnessInstrumentation | None,
) -> ExecutableAgent[Any]:
    """The executable agent over the worker's attempt-bound parts; its run resolves models by `model_resolver`.

    `capabilities` returns the worker's capabilities for one agent of the inline graph, the root included.
    `child_bindings` derives an inline child's run bindings from those the Harness gives it, for what the
    child binds of its own, such as web backends and media understanding. `operators` returns the operator
    through which one async agent of the graph starts child runs over its own edges.
    """
    root = _Host(capabilities, child_bindings, operators, plugins).compose(agent)
    # Each call is priced by its selected model's own pricing; inline subagents inherit the root's policy.
    prices = TokenPricingCapability({model.key: model.pricing for model in agent.models() if model.pricing is not None})
    return HarnessBuilder(instrumentation=instrumentation, configured_plugins_enabled=False).build(
        root.with_updates(capabilities=(*root.capabilities, prices))
    )


def _thread_model(selected: ResolvedModel, native: Model, thread_id: str) -> Model:
    """Bind a detached view, never mutate a client shared by root and child calls."""
    header = selected.provider.config.get("session_affinity_header")
    if isinstance(header, str):
        return RequestHeadersModel(native, common_headers={header: derive_model_affinity_id(thread_id)})
    return native


@dataclass(frozen=True, slots=True)
class _MediaUnderstanding:
    agent: ResolvedAgent
    models: Mapping[str, Model]

    async def understand(
        self, request: MediaUnderstandingRequest, *, usage: ModelUsageBinding | None = None
    ) -> MediaUnderstandingResult:
        # Media calls bypass the primary resolver. Their existing usage binding names the calling Thread.
        if usage is None or usage.owner is None:
            raise MediaUnderstandingError("media_understanding_context_missing")
        provider = AgentMediaUnderstandingProvider(
            models={
                kind: _thread_model(model, self.models[model.key], usage.owner.thread_id)
                for kind, model in self.agent.media.items()
            },
            model_settings={kind: _settings(model, {}) for kind, model in self.agent.media.items()},
            model_ids={kind: model.key for kind, model in self.agent.media.items()},
        )
        return await provider.understand(request, usage=usage)


def media_understanding(agent: ResolvedAgent, models: Mapping[str, Model]) -> _MediaUnderstanding:
    """The agent's media binding, with affinity derived only when its calling Thread is known."""
    return _MediaUnderstanding(agent, models)


@dataclass(frozen=True, slots=True)
class _Host:
    capabilities: HostCapabilities
    child_bindings: ChildBindings | None
    operators: Operators | None
    plugins: HarnessPluginFactoryCatalog

    def compose(self, agent: ResolvedAgent) -> AgentDefinition[Any]:
        config = agent.config
        features: list[AbstractCapability[AgentContext]] = [
            *self.capabilities(agent),
            DynamicEnvironmentCapability(toolsets.environment_configuration(config.toolsets)),
            self._permissions(agent),
            # Compacts at the agent's threshold of the model's context window.
            CompactionCapability(),
        ]
        if (web := toolsets.web_tools(config.toolsets)) is not None:
            features.append(WebCapability(toolsets.web_configuration(web)))
        if config.user_questions:
            features.append(UserInteractionCapability())
        if (client := definition.client_toolset(config)) is not None:
            features.append(ClientToolsCapability(spec=ClientToolsSpec(default_toolsets=(client,))))
        if config.subagents:
            features.append(
                SubagentCapability()
                if config.subagent_mode == "inline"
                else SubagentCapability(
                    async_enabled=True, operator=self.operators(agent) if self.operators is not None else None
                )
            )
        return AgentDefinition(
            agent=AgentSpec(
                model=agent.model.key,
                instructions=config.instructions or None,
                model_settings=cast(dict[str, Any], _settings(agent.model, config.model_settings)) or None,
                retries=None if config.retries is None else AgentRetries(**config.retries.model_dump()),
                model_characteristics=_characteristics(agent),
            ),
            output_type=definition.output_type(config.output_spec),
            # The revision identifies the definition, so a child run can be matched to the edge that spawned it.
            definition_id=agent.revision_id,
            capabilities=tuple(features),
            plugins=tuple(
                self.plugins.create_plugin(
                    HarnessPluginFactoryContext(
                        plugin_key=selected.plugin_key,
                        plugin_id=selected.instance_name,
                        configuration=selected.config,
                        extensions={},
                    )
                )
                for selected in config.plugins
            ),
            subagents=tuple(self._subagent(name, subagent) for name, subagent in agent.subagents.items()),
            model_recovery=ModelRecoveryPolicy(enabled=True),
        )

    def _permissions(self, agent: ResolvedAgent) -> ToolPermissionsCapability:
        permissions = definition.tool_permissions(agent.config, agent.connection_types)
        reviewer = agent.config.reviewer
        if reviewer is None or agent.reviewer is None:
            return ToolPermissionsCapability(permissions)
        review = reviewer.model_copy(
            update={"model_settings": _settings(agent.reviewer, reviewer.model_settings or {})}
        )
        # The run's model resolver resolves `review.model`, the reviewer's model key.
        return ToolPermissionsCapability(permissions, review=review)

    def _subagent(self, name: str, subagent: ResolvedSubagent) -> SubagentDefinition:
        edge, child = subagent.selection, subagent.agent
        return SubagentDefinition(
            name=name,
            description=subagent.description,
            # A child run of its own builds its own definition; its parent needs only the child's identity.
            agent=self.compose(child)
            if child is not None
            else AgentDefinition(agent=AgentSpec(), output_type=str, definition_id=subagent.revision_id),
            context=DelegationContextPolicy(**edge.context.model_dump()),
            usage_limits=edge.usage_limits,
            run_bindings_factory=partial(self.child_bindings, child)
            if child is not None and self.child_bindings is not None
            else None,
        )


def _settings(model: ResolvedModel, settings: Mapping[str, JsonValue]) -> ModelSettings:
    """The agent's native settings layered over the model's own defaults."""
    effective = effective_settings(model.config.model_api, model.config.defaults(), settings)
    if isinstance(headers := effective.get("extra_headers"), dict):
        effective["extra_headers"] = {name.lower(): value for name, value in headers.items()}
    return cast(ModelSettings, effective)


def _characteristics(agent: ResolvedAgent) -> HarnessModelCharacteristics:
    """What the model declares, under the agent's context policy."""
    declared, policy = agent.model.config.characteristics, agent.config.model_characteristics
    return HarnessModelCharacteristics(
        capabilities=declared.capabilities,
        context_window_tokens=policy.context_window_tokens or declared.context_window_tokens,
        proactive_context_management_threshold=policy.proactive_context_management_threshold,
        compact_threshold=policy.compact_threshold,
    )
