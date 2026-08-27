"""Code-first Agent construction and the canonical Harness run stream."""

from __future__ import annotations

import asyncio
import inspect
import typing
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Collection, Coroutine, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, is_dataclass
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from functools import reduce
from operator import or_
from typing import Any, Literal, cast, get_args, get_origin, get_type_hints, overload
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, JsonValue, PydanticSchemaGenerationError, TypeAdapter, ValidationError
from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunEvents
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, ResolveModelId
from pydantic_ai.exceptions import AgentRunError, RunCancelled, UsageLimitExceeded, UserError
from pydantic_ai.messages import AgentStreamEvent, ModelMessage
from pydantic_ai.models import KnownModelName, Model, ModelResolutionContext
from pydantic_ai.output import NativeOutput, OutputSpec, PromptedOutput, StructuredDict, TextOutput, ToolOutput
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage, UsageLimits
from typing_extensions import is_typeddict

from a13n_harness.capabilities.codeact import CODEACT_CAPABILITY_ID, CodeActCapability
from a13n_harness.capabilities.context import (
    COMPACTION_CAPABILITY_ID,
    FILE_CONTEXT_CAPABILITY_ID,
    HANDOFF_CAPABILITY_ID,
    RUNTIME_CONTEXT_CAPABILITY_ID,
    WORKSPACE_OUTLINE_CAPABILITY_ID,
    CompactionCapability,
    CompactionPolicy,
    FileContextCapability,
    HandoffCapability,
    RuntimeContextCapability,
    WorkspaceOutlineCapability,
)
from a13n_harness.capabilities.delegation import (
    DELEGATION_CAPABILITY_ID,
    DELEGATION_RUN_CAPABILITY_ID,
    DelegationCapability,
    DelegationRunCapability,
)
from a13n_harness.capabilities.documents import (
    DOCUMENTS_CAPABILITY_ID,
    DOCUMENTS_RUN_CAPABILITY_ID,
    DocumentsCapability,
    DocumentsRunCapability,
)
from a13n_harness.capabilities.interaction import (
    USER_INTERACTION_CAPABILITY_ID,
    UserInteractionCapability,
)
from a13n_harness.capabilities.lifecycle import (
    LIFECYCLE_EVENT_CAPABILITY_ID,
    LifecycleEventCapability,
)
from a13n_harness.capabilities.media import (
    MEDIA_CAPABILITY_ID,
    MEDIA_RUN_CAPABILITY_ID,
    MediaCapability,
    MediaRunCapability,
)
from a13n_harness.capabilities.process_monitor import (
    MONITORED_PROCESS_CAPABILITY_ID,
    MONITORED_PROCESS_RUN_CAPABILITY_ID,
    MonitoredProcessCapability,
    MonitoredProcessRunCapability,
)
from a13n_harness.capabilities.skills import (
    SKILL_SELECTION_RUN_CAPABILITY_ID,
    SKILLS_CAPABILITY_ID,
    SkillsCapability,
    SkillSelectionRunCapability,
)
from a13n_harness.capabilities.steering import (
    STEERING_CAPABILITY_ID,
    SteeringBridge,
    SteeringCapability,
)
from a13n_harness.capabilities.web import (
    WEB_CAPABILITY_ID,
    WEB_RUN_CAPABILITY_ID,
    WebCapability,
    WebRunCapability,
)
from a13n_harness.capabilities.working_state import (
    TASK_STATE_RUN_CAPABILITY_ID,
    WORKING_STATE_CAPABILITY_ID,
    TaskStateRunCapability,
    WorkingStateCapability,
)
from a13n_harness.capability_types import CapabilityTypeCatalog
from a13n_harness.context import (
    AgentContext,
    BuiltSubagent,
    RunBindings,
    SubagentCollection,
    _CapabilityProvenance,
)
from a13n_harness.environment.dynamic import (
    DYNAMIC_ENVIRONMENT_CAPABILITY_ID,
    DynamicEnvironmentCapability,
)
from a13n_harness.environment.models import EnvironmentError, EnvironmentTopologyChange
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.errors import (
    DefinitionError,
    HarnessError,
    PluginError,
    RunCleanupError,
    RunError,
    StateError,
)
from a13n_harness.events import (
    HarnessEvent,
    HarnessEventEmitter,
    HarnessExtensionEvent,
    HarnessRunResultEvent,
    HarnessStreamEvent,
    _ChildEventForwarder,
    _RunEventEmitter,
)
from a13n_harness.filters.integrity import (
    MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID,
    MessageIntegrityFilterCapability,
)
from a13n_harness.input import (
    RunInputFactory,
    RunInputValue,
    RunPreparationContext,
    SemanticRunInput,
    normalize_input,
)
from a13n_harness.model_context import (
    MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID,
    ModelContextCoordinatorCapability,
)
from a13n_harness.models.binding import resolve_run_model
from a13n_harness.plugin_configuration import HarnessBuildContext, HarnessPluginConfiguration
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
    build_harness_plugin_factory_catalog,
)
from a13n_harness.plugins import (
    AbstractHarnessPlugin,
    BoundPluginContext,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
    bind_agent_plugins,
    bind_run_plugins,
)
from a13n_harness.recovery import (
    InterruptedResponseTracker,
    ModelRecoveryPolicy,
    is_recoverable_model_failure,
    normalize_interrupted_history,
)
from a13n_harness.result import HarnessRunResult, SafeFailure
from a13n_harness.spec import AgentSpec as HarnessAgentSpec
from a13n_harness.state import AgentContextState, HarnessState
from a13n_harness.tools.client import (
    CLIENT_TOOLS_CAPABILITY_ID,
    CLIENT_TOOLS_RUN_CAPABILITY_ID,
    ClientToolsCapability,
    ClientToolsRunCapability,
)
from a13n_harness.tools.deferred import (
    DeferredToolResume,
    bind_managed_approval_identities,
    preflight_deferred_resume,
)
from a13n_harness.tools.invocation import (
    TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID,
    ToolExecutionBoundaryCapability,
)
from a13n_harness.tools.policy import INVOCATION_POLICY_CAPABILITY_ID, InvocationPolicyCapability
from a13n_harness.tools.surface import (
    TOOL_SURFACE_CAPABILITY_ID,
    ToolSurfaceCapability,
)
from a13n_harness.usage import (
    MODEL_COST_RUN_CAPABILITY_ID,
    USAGE_CAPABILITY_ID,
    ModelCostRunCapability,
    RunUsageLedger,
    UsageCapability,
)

_AGENT_EVENT_ADAPTER = TypeAdapter(AgentStreamEvent)
_EXTENSION_EVENT_ADAPTER = TypeAdapter(HarnessExtensionEvent)
_EMPTY_CAPABILITY_TYPE_CATALOG = CapabilityTypeCatalog()


@dataclass(frozen=True, slots=True)
class _ResponsePumpTerminal:
    error: BaseException | None = None


@dataclass(slots=True)
class _TopologyEventDrain:
    requested: asyncio.Event = field(default_factory=asyncio.Event)
    drained: asyncio.Event = field(default_factory=asyncio.Event)
    progress: asyncio.Event = field(default_factory=asyncio.Event)
    cursor: int | None = None
    terminal_version: int | None = None


def _consume_finished_task(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def _stop_environment_event_task(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    try:
        done, pending = await asyncio.wait((task,), timeout=5.0)
    except BaseException:
        if not task.done():
            task.add_done_callback(_consume_finished_task)
        raise
    if pending:
        task.add_done_callback(_consume_finished_task)
        raise RunError(
            "Environment topology event adapter did not stop before cleanup deadline.",
            code="event_adapter_cleanup_timeout",
        )
    if task in done and not task.cancelled():
        task.result()


async def _emit_environment_topology_events(
    context: AgentContext,
    drain: _TopologyEventDrain,
) -> None:
    """Adapt the non-draining Environment journal through the logical terminal fence."""
    observer = context.environment.topology_observer
    cursor = observer.initial_topology_version
    drain.cursor = cursor
    while True:
        terminal_version = drain.terminal_version
        if terminal_version is not None and cursor >= terminal_version:
            drain.drained.set()
            drain.progress.set()
            return

        read_task = asyncio.create_task(observer.read(after_version=cursor, wait=True))
        terminal_task = asyncio.create_task(drain.requested.wait())
        try:
            done, _ = await asyncio.wait({read_task, terminal_task}, return_when=asyncio.FIRST_COMPLETED)
            if terminal_task in done:
                if not read_task.done():
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                changes = await observer.read(after_version=cursor, wait=False)
            else:
                changes = read_task.result()
        except EnvironmentError as exc:
            if exc.code == "environment_closed":
                terminal_version = drain.terminal_version
                if terminal_version is not None and cursor >= terminal_version:
                    drain.drained.set()
                    drain.progress.set()
                return
            raise
        finally:
            for task in (read_task, terminal_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(read_task, terminal_task, return_exceptions=True)

        for change in changes:
            try:
                await context.events.emit(_environment_topology_event(change))
            except RunError as exc:
                if exc.code in {"event_emitter_closed", "event_consumer_stopped"}:
                    return
                raise
            cursor = change.current_version
            progress = drain.progress
            drain.cursor = cursor
            drain.progress = asyncio.Event()
            progress.set()


def _environment_topology_event(change: EnvironmentTopologyChange) -> HarnessExtensionEvent:
    projected: list[dict[str, JsonValue]] = [
        {
            "kind": item.kind,
            "binding_id": item.binding_id,
            "previous_revision": item.previous_revision,
            "current_revision": item.current_revision,
            "previous_alias": item.previous_alias,
            "current_alias": item.current_alias,
        }
        for item in change.bindings[:128]
    ]
    payload: dict[str, JsonValue] = {
        "type": "environment_topology_changed",
        "previous_version": change.previous_version,
        "current_version": change.current_version,
        "request_digest": change.request_digest,
        "binding_change_count": len(change.bindings),
        "bindings": cast(JsonValue, projected),
        "bindings_truncated": len(projected) < len(change.bindings),
    }
    return HarnessExtensionEvent(kind="context", payload=payload)


@dataclass(frozen=True, slots=True)
class DelegationContextPolicy:
    """Portable ceilings on context and working-state sharing for one child edge."""

    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"

    def __post_init__(self) -> None:
        if not isinstance(self.include_task, bool):
            raise DefinitionError("Subagent include_task policy must be a boolean.", code="subagent_context_invalid")
        if self.history not in {"none", "summary", "selected"}:
            raise DefinitionError("Unsupported subagent history policy.", code="subagent_context_invalid")
        if self.task_state not in {"shared", "isolated"}:
            raise DefinitionError("Unsupported subagent task-state policy.", code="subagent_context_invalid")


@dataclass(frozen=True, slots=True)
class SubagentDefinition:
    """One named process-local child definition and authored edge ceilings."""

    name: str
    description: str
    agent: AgentDefinition[Any]
    context: DelegationContextPolicy = field(default_factory=DelegationContextPolicy)
    usage_limits: UsageLimits | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DefinitionError("Subagent name must be a non-blank string.", code="subagent_name_invalid")
        if not isinstance(self.description, str) or not self.description.strip():
            raise DefinitionError(
                "Subagent description must be a non-blank string.",
                code="subagent_description_invalid",
            )
        if not isinstance(self.agent, AgentDefinition):
            raise DefinitionError("Subagent agent must be an AgentDefinition.", code="subagent_definition_invalid")
        if not isinstance(self.context, DelegationContextPolicy):
            raise DefinitionError("Subagent context must be DelegationContextPolicy.", code="subagent_context_invalid")
        if self.usage_limits is not None and not isinstance(self.usage_limits, UsageLimits):
            raise DefinitionError("Subagent usage_limits must be UsageLimits or None.", code="subagent_limits_invalid")
        object.__setattr__(self, "usage_limits", deepcopy(self.usage_limits))


@dataclass(frozen=True, slots=True)
class AgentDefinition[OutputT]:
    """Immutable code-first inputs for one process-local executable Agent."""

    agent: AgentSpec
    output_type: OutputSpec[OutputT] | None
    definition_id: str = field(default_factory=lambda: str(uuid4()))
    model: Model | KnownModelName | str | None = None
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    plugins: tuple[AbstractHarnessPlugin, ...] = ()
    subagents: tuple[SubagentDefinition, ...] = ()
    model_recovery: ModelRecoveryPolicy = field(default_factory=ModelRecoveryPolicy)

    def __post_init__(self) -> None:
        if not self.definition_id.strip():
            raise DefinitionError("definition_id must not be blank.", code="definition_id_invalid")
        has_schema = self.agent.output_schema is not None
        if self.output_type is not None and has_schema:
            raise DefinitionError(
                "AgentDefinition.output_type and AgentSpec.output_schema are mutually exclusive.",
                code="output_contract_conflict",
            )
        if self.output_type is None and not has_schema:
            raise DefinitionError(
                "AgentDefinition requires an explicit output_type or AgentSpec.output_schema.",
                code="output_contract_missing",
            )
        if self.output_type is not None and _output_spec_contains_deferred_requests(self.output_type):
            raise DefinitionError(
                "DeferredToolRequests is reserved for Harness suspension and cannot be a business output.",
                code="output_contract_reserved",
            )
        object.__setattr__(self, "agent", self.agent.model_copy(deep=True))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "plugins", tuple(self.plugins))
        subagents = tuple(self.subagents)
        if not all(isinstance(child, SubagentDefinition) for child in subagents):
            raise DefinitionError(
                "subagents must contain only SubagentDefinition values.",
                code="subagent_definition_invalid",
            )
        names = [child.name for child in subagents]
        if len(set(names)) != len(names):
            raise DefinitionError("Subagent names must be unique within one parent.", code="subagent_name_duplicate")
        object.__setattr__(self, "subagents", subagents)


def _resolve_model_configured_capabilities(
    agent: AgentSpec,
    capabilities: tuple[AbstractCapability[AgentContext], ...],
) -> tuple[AbstractCapability[AgentContext], ...]:
    model_configuration = agent.model_configuration if isinstance(agent, HarnessAgentSpec) else None
    resolved: list[AbstractCapability[AgentContext]] = []
    for capability in capabilities:
        if isinstance(capability, CompactionCapability) and capability.policy is None:
            trigger_tokens = model_configuration.compaction_trigger_tokens if model_configuration is not None else None
            if trigger_tokens is None:
                raise DefinitionError(
                    "Automatic compaction requires AgentSpec.model_config.context_window.",
                    code="compaction_policy_unresolved",
                )
            resolved.append(CompactionCapability(CompactionPolicy(trigger_tokens=trigger_tokens)))
            continue
        if isinstance(capability, HandoffCapability) and model_configuration is not None:
            configuration = capability.configuration
            threshold_fields = {"include_summary_reminder", "summary_reminder_tokens"}
            if not threshold_fields.intersection(configuration.model_fields_set):
                proactive_threshold = model_configuration.proactive_context_management_threshold
                if proactive_threshold is None:
                    configuration = configuration.model_copy(
                        update={"include_summary_reminder": False},
                        deep=True,
                    )
                    resolved.append(HandoffCapability(configuration))
                    continue
                if model_configuration.context_window is not None:
                    reminder_tokens = model_configuration.summary_reminder_tokens
                    assert reminder_tokens is not None
                    configuration = configuration.model_copy(
                        update={"summary_reminder_tokens": reminder_tokens},
                        deep=True,
                    )
                    resolved.append(HandoffCapability(configuration))
                    continue
        resolved.append(capability)
    return tuple(resolved)


class HarnessBuilder:
    """Build executable Agents through one authoritative Agent.from_spec path."""

    def __init__(
        self,
        *,
        capability_type_catalog: CapabilityTypeCatalog | None = None,
        build_context: HarnessBuildContext | None = None,
        configured_plugins_enabled: bool | None = None,
    ) -> None:
        if capability_type_catalog is not None and not isinstance(capability_type_catalog, CapabilityTypeCatalog):
            raise DefinitionError(
                "capability_type_catalog must be a CapabilityTypeCatalog or None.",
                code="capability_type_catalog_invalid",
            )
        if build_context is not None and not isinstance(build_context, HarnessBuildContext):
            raise PluginError(
                "build_context must be a HarnessBuildContext or None.",
                code="plugin_configuration_invalid",
            )
        if configured_plugins_enabled is not None and not isinstance(configured_plugins_enabled, bool):
            raise PluginError(
                "configured_plugins_enabled must be a boolean or None.",
                code="plugin_configuration_enablement_invalid",
            )
        self._capability_type_catalog = capability_type_catalog or _EMPTY_CAPABILITY_TYPE_CATALOG
        if build_context is None:
            resolved_build_context = HarnessBuildContext.from_environment(enabled=configured_plugins_enabled)
        else:
            effective_application = (
                build_context.configured_plugins_enabled
                if configured_plugins_enabled is None
                else configured_plugins_enabled
            )
            if effective_application and not isinstance(
                build_context.plugin_configuration,
                HarnessPluginConfiguration,
            ):
                raise PluginError(
                    "Enabling configured plugins in an explicit build context requires a configuration document.",
                    code="plugin_configuration_invalid",
                )
            resolved_build_context = HarnessBuildContext(
                configured_plugins_enabled=effective_application,
                plugin_configuration=build_context.plugin_configuration if effective_application else None,
                extensions=build_context.extensions,
            )
        self._build_context = HarnessBuildContext(
            configured_plugins_enabled=resolved_build_context.configured_plugins_enabled,
            plugin_configuration=resolved_build_context.plugin_configuration,
            extensions=resolved_build_context.extensions,
        )
        self._plugin_factory_catalog: HarnessPluginFactoryCatalog | None = None
        if self._build_context.configured_plugins_enabled:
            configuration = self._build_context.plugin_configuration
            if not isinstance(configuration, HarnessPluginConfiguration):
                raise PluginError(
                    "Enabled configured plugins require a configuration document.",
                    code="plugin_configuration_invalid",
                )
            selected_keys = tuple(dict.fromkeys(entry.plugin_key for entry in configuration.enabled_plugins))
            self._plugin_factory_catalog = build_harness_plugin_factory_catalog(
                plugin_keys=selected_keys,
            )

    def build[BuildOutputT](self, definition: AgentDefinition[BuildOutputT]) -> ExecutableAgent[BuildOutputT]:
        """Validate code-first composition and recursively construct a reusable executable."""
        return self._build(definition, active_definition_ids=())

    def _build[BuildOutputT](
        self,
        definition: AgentDefinition[BuildOutputT],
        *,
        active_definition_ids: tuple[int, ...],
    ) -> ExecutableAgent[BuildOutputT]:
        definition_object_id = id(definition)
        if definition_object_id in active_definition_ids:
            raise DefinitionError("Subagent definitions must form a finite acyclic graph.", code="subagent_cycle")
        child_path = (*active_definition_ids, definition_object_id)
        built_children = tuple(
            BuiltSubagent(
                declaration=child,
                definition=child.agent,
                executable=self._build(child.agent, active_definition_ids=child_path),
            )
            for child in definition.subagents
        )
        subagents = SubagentCollection({child.declaration.name: child for child in built_children})
        configured_plugins = self._create_configured_plugins()
        plugins, plugin_capabilities = bind_agent_plugins((*definition.plugins, *configured_plugins))
        authored_capabilities = _resolve_model_configured_capabilities(
            definition.agent,
            (*definition.capabilities, *plugin_capabilities),
        )
        definition_reserved_ids = _validate_capability_source(authored_capabilities, source="definition")

        async def resolve_model(
            context: ModelResolutionContext[AgentContext],
            model_id: str,
        ) -> Model | None:
            return await resolve_run_model(context, model_id)

        capabilities = (
            ToolExecutionBoundaryCapability(),
            ToolSurfaceCapability(),
            MessageIntegrityFilterCapability(),
            LifecycleEventCapability(),
            SteeringCapability(),
            ModelContextCoordinatorCapability(),
            ResolveModelId(resolve_model),
            *authored_capabilities,
            UsageCapability(),
        )
        try:
            construction_spec, business_output, output_adapter = _resolve_business_output(definition)
            complete_output = [business_output, DeferredToolRequests]
            agent = Agent.from_spec(
                construction_spec,
                deps_type=AgentContext,
                custom_capability_types=self._capability_type_catalog.custom_capability_types,
                model=definition.model,
                output_type=complete_output,
                capabilities=capabilities,
                defer_model_check=True,
            )
            _validate_built_capability_tree(
                agent.root_capability,
                definition_reserved_ids=definition_reserved_ids,
            )
        except Exception as exc:
            if isinstance(exc, HarnessError):
                raise
            raise DefinitionError(
                "Pydantic AI Agent construction failed.",
                code="agent_build_failed",
                details={"definition_id": definition.definition_id},
            ) from exc
        return ExecutableAgent(
            definition=definition,
            agent=cast(Agent[AgentContext, BuildOutputT | DeferredToolRequests], agent),
            output_adapter=output_adapter,
            plugins=plugins,
            subagents=subagents,
            definition_reserved_capability_ids=definition_reserved_ids,
        )

    def _create_configured_plugins(self) -> tuple[AbstractHarnessPlugin, ...]:
        catalog = self._plugin_factory_catalog
        if catalog is None:
            return ()
        configuration = self._build_context.plugin_configuration
        if not isinstance(configuration, HarnessPluginConfiguration):
            raise PluginError(
                "Enabled configured plugins require a configuration document.",
                code="plugin_configuration_invalid",
            )
        return tuple(
            catalog.create_plugin(
                HarnessPluginFactoryContext(
                    plugin_key=entry.plugin_key,
                    plugin_id=entry.plugin_id,
                    configuration=entry.configuration,
                    extensions=self._build_context.extensions,
                )
            )
            for entry in configuration.enabled_plugins
        )

    @overload
    def build_code[BuildOutputT](
        self,
        agent: AgentSpec,
        *,
        output_type: OutputSpec[BuildOutputT],
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[BuildOutputT]: ...

    @overload
    def build_code(
        self,
        agent: AgentSpec,
        *,
        output_type: None,
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[dict[str, JsonValue]]: ...

    def build_code(
        self,
        agent: AgentSpec,
        *,
        output_type: OutputSpec[Any] | None,
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
        subagents: Sequence[SubagentDefinition] = (),
        model_recovery: ModelRecoveryPolicy | None = None,
    ) -> ExecutableAgent[Any]:
        """Convenience constructor retaining the same AgentDefinition build path."""
        return self.build(
            AgentDefinition(
                agent=agent,
                output_type=output_type,
                definition_id=definition_id or str(uuid4()),
                model=model,
                capabilities=tuple(capabilities),
                plugins=tuple(plugins),
                subagents=tuple(subagents),
                model_recovery=model_recovery or ModelRecoveryPolicy(),
            )
        )


class ExecutableAgent[OutputT]:
    """Reusable code-built Agent that creates one fresh context per invocation."""

    def __init__(
        self,
        *,
        definition: AgentDefinition[OutputT],
        agent: Agent[AgentContext, OutputT | DeferredToolRequests],
        output_adapter: TypeAdapter[Any],
        plugins: tuple[AbstractHarnessPlugin, ...],
        subagents: SubagentCollection,
        definition_reserved_capability_ids: frozenset[str],
    ) -> None:
        self.definition = definition
        self.subagents = subagents
        self._agent = agent
        self._output_adapter = output_adapter
        self._plugins = plugins
        self._definition_reserved_capability_ids = definition_reserved_capability_ids
        self._closed = False

    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]:
        """Consume the canonical stream and return its sole terminal result."""
        async with self.stream(
            input,
            input_factory=input_factory,
            bindings=bindings,
            previous_state=previous_state,
            deferred_resume=deferred_resume,
            usage=usage,
            usage_limits=usage_limits,
        ) as stream:
            async for item in stream:
                if isinstance(item, HarnessRunResultEvent):
                    return item.result
        raise RunError("The run ended without a terminal result.", code="run_result_missing")

    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        deferred_resume: DeferredToolResume | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]:
        """Create a lazy, single-entry canonical Harness stream."""
        if self._closed:
            raise RunError("The executable is closed.", code="executable_closed")
        if input is not None and input_factory is not None:
            raise RunError(
                "input and input_factory are mutually exclusive.",
                code="input_source_conflict",
            )
        run_reserved_ids = _validate_capability_source(bindings.capabilities, source="run")
        skill_selection_names = _capture_skill_selection_names(bindings.capabilities)
        normalized_resume = (
            preflight_deferred_resume(deferred_resume, previous_state=previous_state)
            if deferred_resume is not None
            else None
        )
        return HarnessRunStream(
            executable=self,
            input=input,
            input_factory=input_factory,
            bindings=bindings,
            previous_state=previous_state,
            deferred_resume=normalized_resume,
            run_reserved_capability_ids=run_reserved_ids,
            skill_selection_names=skill_selection_names,
            usage=usage,
            usage_limits=usage_limits,
        )

    async def __aenter__(self) -> ExecutableAgent[OutputT]:
        if self._closed:
            raise RunError("The executable is closed.", code="executable_closed")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Idempotently close recursively owned children and prevent future runs."""
        if self._closed:
            return
        self._closed = True
        for child in reversed(tuple(self.subagents.values())):
            await child.executable.close()


class HarnessRunStream[OutputT](AsyncIterator[HarnessStreamEvent[OutputT]]):
    """One lazy Pydantic AgentRunEvents stream plus Harness middleware and teardown."""

    def __init__(
        self,
        *,
        executable: ExecutableAgent[OutputT],
        input: RunInputValue | None,
        input_factory: RunInputFactory | None,
        bindings: RunBindings,
        previous_state: HarnessState | None,
        deferred_resume: DeferredToolResume | None,
        run_reserved_capability_ids: frozenset[str],
        skill_selection_names: frozenset[str] | None,
        usage: RunUsage | None,
        usage_limits: UsageLimits | None,
    ) -> None:
        self._executable = executable
        self._input = input
        self._input_factory = input_factory
        self._bindings = bindings
        self._previous_state = (
            previous_state.model_copy(deep=True) if previous_state is not None else HarnessState.new()
        )
        self.thread_id = self._previous_state.thread_id
        self.run_id = f"run-{uuid4().hex}"
        self._deferred_resume = deferred_resume
        self._run_reserved_capability_ids = run_reserved_capability_ids
        self._skill_selection_names = skill_selection_names
        self._usage = usage if usage is not None else RunUsage()
        self._usage_limits = usage_limits
        self._emitter = _RunEventEmitter(self.thread_id, self.run_id)
        self._environment_ready: asyncio.Future[BoundEnvironment] | None = None
        self._environment_close_requested = asyncio.Event()
        self._environment_lifecycle_task: asyncio.Task[None] | None = None
        self._environment_ready_delivered = False
        self._context: AgentContext | None = None
        self._response: PluginRunResponse[OutputT] | None = None
        self._responses: list[tuple[int, PluginRunResponse[OutputT]]] = []
        self._response_ids: set[int] = set()
        self._closed_response_ids: set[int] = set()
        self._pydantic_events: AgentRunEvents[OutputT | DeferredToolRequests] | None = None
        self._topology_event_drain = _TopologyEventDrain()
        self._environment_event_task: asyncio.Task[None] | None = None
        self._response_pump_task: asyncio.Task[None] | None = None
        self._response_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
        self._response_next_task: asyncio.Task[Any] | None = None
        self._terminal_close_task: asyncio.Task[None] | None = None
        self._pending_terminal_result: HarnessRunResult[OutputT] | None = None
        self._source_cleanup_failures: list[BaseException] = []
        self._run_attachments_closed = False
        self._logical_events_started = False
        self._latest_messages: tuple[ModelMessage, ...] = self._previous_state.message_history
        self._new_message_index = len(self._latest_messages)
        self._source_sequence = 0
        self._public_sequence = 0
        self._last_public_child_sequence_by_run: dict[str, int] = {}
        self._last_valid_outcome: HarnessRunResult[OutputT] | None = None
        self._result: HarnessRunResult[OutputT] | None = None
        self._entered = False
        self._iterated = False
        self._closed = False
        self._terminal_yielded = False
        self._cancel_requested = False
        self._cancel_event = asyncio.Event()
        self._next_active = False

    @property
    def context(self) -> AgentContext:
        """Return the fresh run context after stream entry."""
        if self._context is None:
            raise RunError("The stream is not entered.", code="run_not_active")
        return self._context

    @property
    def result(self) -> HarnessRunResult[OutputT] | None:
        """Return the terminal result only after its result event was delivered."""
        return self._result if self._terminal_yielded else None

    @property
    def usage(self) -> RunUsage:
        """Return the live Pydantic AI usage accumulator."""
        return self._usage

    def _bind_parent_event_forwarder(self, parent: HarnessEventEmitter) -> _ChildEventForwarder:
        """Bind this exact stream as a validated child of one active parent emitter."""
        if not isinstance(parent, _RunEventEmitter):
            raise RunError("Inline child parent emitter is invalid.", code="child_event_invalid")
        return parent.bind_child(self._emitter)

    async def __aenter__(self) -> HarnessRunStream[OutputT]:
        if self._entered:
            raise RunError("HarnessRunStream cannot be entered more than once.", code="run_stream_reused")
        self._entered = True
        try:
            self._environment_ready = asyncio.get_running_loop().create_future()
            self._environment_lifecycle_task = asyncio.create_task(self._run_environment_lifecycle())
            environment = await asyncio.shield(self._environment_ready)
            self._environment_ready_delivered = True
            preparation = RunPreparationContext(
                run_id=self.run_id,
                instance=self._bindings.instance,
                environment=environment,
                metadata=self._bindings.metadata,
            )
            input_value = self._input
            if self._input_factory is not None:
                try:
                    input_value = await self._input_factory(preparation)
                except Exception as exc:
                    raise RunError("Run input factory failed.", code="input_factory_failed") from exc
            semantic_input = normalize_input(input_value)
            plugin_context = BoundPluginContext()
            usage_attribution = RunUsageLedger(
                run_id=self.run_id,
                instance=self._bindings.instance,
                events=self._emitter,
            )
            context_state = AgentContextState(self._previous_state.agent_context_state)
            context = AgentContext(
                run_id=self.run_id,
                thread_id=self._previous_state.thread_id,
                instance=self._bindings.instance,
                state=context_state,
                environment=environment,
                model_binding=self._bindings.model_binding,
                model_context=self._bindings.model_context,
                plugins=plugin_context,
                subagents=self._executable.subagents,
                events=self._emitter,
                usage_attribution=usage_attribution,
                deferred_resume=self._deferred_resume,
                metadata=self._bindings.metadata,
                _steering=SteeringBridge(
                    context_state,
                    run_id=self.run_id,
                    retain_inputs=COMPACTION_CAPABILITY_ID in self._executable._definition_reserved_capability_ids,
                ),
                _skill_selection_names=self._skill_selection_names,
                _capability_provenance=_CapabilityProvenance(
                    definition_ids=self._executable._definition_reserved_capability_ids,
                    run_ids=self._run_reserved_capability_ids,
                ),
            )
            self._context = context
            run_plugins = await bind_run_plugins(self._executable._plugins, context)
            exchange = PluginRunExchange(
                input=semantic_input,
                context=context,
                _state_exporter=self.export_state,
            )
            self._response = self._build_response(run_plugins, 0, exchange)
            return self
        except asyncio.CancelledError as exc:
            await self._close_resources(outcome=None, cancellation=exc)
            raise
        except BaseException:
            await self._close_resources(outcome=None)
            raise

    async def _run_environment_lifecycle(self) -> None:
        ready = self._environment_ready
        assert ready is not None
        try:
            async with self._bindings.environment.bind(
                run_id=self.run_id,
                instance=self._bindings.instance,
            ) as environment:
                if self._previous_state.environment_state is not None:
                    await environment.restore_state(self._previous_state.environment_state)
                await environment.activate()
                if not ready.done():
                    ready.set_result(environment)
                await self._environment_close_requested.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
                return
            raise

    async def _close_environment_lifecycle(self) -> None:
        task = self._environment_lifecycle_task
        self._environment_lifecycle_task = None
        if task is None:
            return

        abort_entry = not self._environment_ready_delivered
        if abort_entry:
            ready = self._environment_ready
            if ready is not None and not ready.done():
                ready.cancel()
            if not task.done():
                task.cancel()
        else:
            self._environment_close_requested.set()

        pending_cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if current_task is not None and current_task.cancelling():
                    pending_cancellation = pending_cancellation or exc
                    if abort_entry and not task.done():
                        task.cancel()
                continue

        lifecycle_error: BaseException | None = None
        if task.cancelled():
            if not abort_entry:
                lifecycle_error = asyncio.CancelledError("Environment lifecycle was cancelled during cleanup.")
        else:
            lifecycle_error = task.exception()
        if pending_cancellation is not None:
            if lifecycle_error is not None:
                pending_cancellation.add_note(f"Environment lifecycle cleanup also failed: {lifecycle_error!r}")
            raise pending_cancellation
        if lifecycle_error is not None:
            raise lifecycle_error

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, traceback
        if self._terminal_close_task is not None:
            await self._terminal_close_task
        elif not self._closed:
            cancellation = exc_value if isinstance(exc_value, asyncio.CancelledError) else None
            await self._close_resources(outcome=self._last_valid_outcome, cancellation=cancellation)

    def __aiter__(self) -> HarnessRunStream[OutputT]:
        if not self._entered or self._closed:
            raise RunError("The stream is not active.", code="run_not_active")
        if self._iterated:
            raise RunError("HarnessRunStream has exactly one consumer.", code="run_stream_reused")
        self._iterated = True
        return self

    async def __anext__(self) -> HarnessStreamEvent[OutputT]:
        if not self._entered or self._terminal_yielded or (self._closed and self._pending_terminal_result is None):
            raise StopAsyncIteration
        if self._next_active:
            raise RunError(
                "Concurrent iteration of HarnessRunStream is not supported.",
                code="run_stream_concurrent_next",
            )
        self._next_active = True
        try:
            return await self._next_item()
        finally:
            self._next_active = False

    async def _next_item(self) -> HarnessStreamEvent[OutputT]:
        assert self._response is not None
        try:
            self._start_logical_event_mux()
            while True:
                if self._pending_terminal_result is not None:
                    close_task = self._terminal_close_task
                    assert close_task is not None
                    await close_task
                    result = self._pending_terminal_result
                    self._pending_terminal_result = None
                    self._terminal_close_task = None
                    self._result = result
                    self._terminal_yielded = True
                    return HarnessRunResultEvent(
                        thread_id=self.thread_id,
                        run_id=self.run_id,
                        sequence=self._next_public_sequence(),
                        occurred_at=datetime.now(UTC),
                        result=result,
                    )

                self._ensure_response_next_task()
                assert self._response_next_task is not None
                task = self._response_next_task
                wait_for: set[asyncio.Task[Any]] = {task}
                lifecycle_task = self._environment_lifecycle_task
                if lifecycle_task is not None:
                    wait_for.add(lifecycle_task)
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
                if lifecycle_task is not None and lifecycle_task in done:
                    self._environment_lifecycle_task = None
                    if lifecycle_task.cancelled():
                        raise RunError(
                            "Environment lifecycle stopped before logical run cleanup.",
                            code="environment_lifecycle_stopped",
                        )
                    error = lifecycle_task.exception()
                    if error is not None:
                        raise error
                    raise RunError(
                        "Environment lifecycle stopped before logical run cleanup.",
                        code="environment_lifecycle_stopped",
                    )
                item = task.result()
                self._response_next_task = None
                if isinstance(item, _ResponsePumpTerminal):
                    if item.error is not None:
                        raise item.error
                    raise StopAsyncIteration
                if isinstance(item, HarnessRunResult):
                    result = self._validate_result_candidate(item)
                    self._last_valid_outcome = result
                    await self._stop_response_pump(cancel=False)
                    self._pending_terminal_result = result
                    self._terminal_close_task = asyncio.create_task(self._close_resources(outcome=result))
                    continue
                return self._public_event(item)
        except StopAsyncIteration as exc:
            error = PluginError(
                "Plugin middleware ended without a result candidate.",
                code="plugin_result_missing",
            )
            error.__cause__ = exc
            await self._raise_after_failure(error)
        except asyncio.CancelledError as exc:
            if self._terminal_close_task is not None:
                try:
                    await self._terminal_close_task
                except BaseException as cleanup:
                    exc.add_note(f"Harness cleanup also failed: {cleanup!r}")
                raise
            await self._close_resources(outcome=self._last_valid_outcome, cancellation=exc)
            raise
        except RunCleanupError as exc:
            if self._closed:
                raise
            await self._raise_after_failure(exc)
        except BaseException as exc:
            await self._raise_after_failure(exc)
        raise AssertionError("unreachable")

    def _start_logical_event_mux(self) -> None:
        if self._logical_events_started:
            return
        self._logical_events_started = True
        self._emitter.start_consuming()
        self._topology_event_drain.cursor = self.context.environment.topology_observer.initial_topology_version
        self._environment_event_task = asyncio.create_task(
            _emit_environment_topology_events(self.context, self._topology_event_drain)
        )
        self._response_pump_task = asyncio.create_task(self._pump_response())

    def _install_terminal_fence(self) -> None:
        drain = self._topology_event_drain
        if drain.terminal_version is not None:
            return
        self._bindings.environment.controller.begin_close()
        drain.terminal_version = self.context.environment.topology.topology_version
        drain.requested.set()

    async def _pump_response(self) -> None:
        assert self._response is not None
        response = self._response
        iteration_error: BaseException | None = None
        cancellation: asyncio.CancelledError | None = None
        terminal_result: HarnessRunResult[OutputT] | None = None
        try:
            async for item in response:
                if isinstance(item, HarnessRunResult):
                    if terminal_result is not None:
                        raise PluginError(
                            "Plugin middleware emitted more than one result candidate.",
                            code="plugin_result_multiple",
                        )
                    terminal_result = item
                    self._install_terminal_fence()
                    continue
                await self._response_queue.put(item)
        except asyncio.CancelledError as exc:
            cancellation = exc
            current_task = asyncio.current_task()
            if current_task is not None:
                while current_task.cancelling():
                    current_task.uncancel()
        except BaseException as exc:
            if terminal_result is not None:
                self._source_cleanup_failures.append(exc)
            else:
                iteration_error = exc
        try:
            await self._close_registered_responses()
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except BaseException as exc:
            self._source_cleanup_failures.append(exc)
        if cancellation is not None:
            raise cancellation
        if terminal_result is not None:
            await self._response_queue.put(terminal_result)
            return
        await self._response_queue.put(_ResponsePumpTerminal(error=iteration_error))

    async def _close_registered_responses(self) -> None:
        failures: list[BaseException] = []
        pending_cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()

        def capture_cancellation(exc: asyncio.CancelledError | None = None) -> bool:
            nonlocal pending_cancellation
            if current_task is None or not current_task.cancelling():
                return False
            pending_cancellation = pending_cancellation or exc or asyncio.CancelledError()
            while current_task.cancelling():
                current_task.uncancel()
            return True

        responses = sorted(self._responses, key=lambda item: item[0], reverse=True)
        for _, response in responses:
            response_id = id(response)
            if response_id in self._closed_response_ids:
                continue
            self._closed_response_ids.add(response_id)
            try:
                await response.aclose()
            except asyncio.CancelledError as exc:
                if not capture_cancellation(exc):
                    failures.append(exc)
            except BaseException as exc:
                failures.append(exc)
            finally:
                capture_cancellation()
        if pending_cancellation is not None:
            self._source_cleanup_failures.extend(failures)
            for failure in failures:
                pending_cancellation.add_note(f"Harness plugin response cleanup also failed: {failure!r}")
            raise pending_cancellation
        if len(failures) == 1:
            failure = failures[0]
            if isinstance(failure, asyncio.CancelledError):
                raise BaseExceptionGroup("Harness plugin response cleanup failed", failures)
            raise failure
        if failures:
            raise BaseExceptionGroup("Harness plugin response cleanup failed", failures)

    def _ensure_response_next_task(self) -> None:
        if self._response_next_task is None:
            self._response_next_task = asyncio.create_task(self._response_queue.get())

    async def _stop_response_pump(self, *, cancel: bool = True) -> None:
        task = self._response_pump_task
        self._response_pump_task = None
        if task is None:
            return
        if cancel and not task.done():
            task.cancel()
        pending_cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if current_task is not None and current_task.cancelling():
                    pending_cancellation = pending_cancellation or exc
                    if not task.done():
                        task.cancel()
                continue
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._source_cleanup_failures.append(error)
        if pending_cancellation is not None:
            raise pending_cancellation

    async def _cancel_logical_source_tasks(self) -> None:
        task = self._response_next_task
        self._response_next_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._stop_response_pump()

    def _public_event(self, item: Any) -> HarnessEvent:
        if isinstance(item, HarnessExtensionEvent):
            item = HarnessEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                sequence=0,
                occurred_at=datetime.now(UTC),
                event=item,
            )
        if not isinstance(item, HarnessEvent) or item.sequence < 0:
            raise PluginError("Plugin emitted an invalid stream item.", code="plugin_event_invalid")
        try:
            event = (
                _EXTENSION_EVENT_ADAPTER.validate_python(
                    item.event.model_dump(),
                    strict=True,
                )
                if isinstance(item.event, HarnessExtensionEvent)
                else _AGENT_EVENT_ADAPTER.validate_python(item.event, strict=True)
            )
        except ValidationError as exc:
            raise PluginError(
                "Plugin emitted an invalid Harness event.",
                code="plugin_event_invalid",
            ) from exc
        provenance = self._emitter.take_child_provenance(item)
        if provenance is not None and (item.thread_id != provenance.thread_id or item.run_id != provenance.run_id):
            raise PluginError(
                "Plugin changed forwarded child event provenance.",
                code="plugin_event_run_mismatch",
            )
        if provenance is not None and item.sequence != provenance.sequence:
            raise PluginError(
                "Plugin changed forwarded child event sequence.",
                code="plugin_event_sequence_invalid",
            )
        if item.run_id != self.run_id:
            if provenance is None or not self._emitter.is_registered_child(item.run_id, item.thread_id):
                raise PluginError(
                    "Plugin emitted an event for an unregistered child run.",
                    code="plugin_event_run_mismatch",
                )
            previous = self._last_public_child_sequence_by_run.get(item.run_id)
            if previous is not None and item.sequence <= previous:
                raise PluginError(
                    "Plugin emitted an out-of-order child event.",
                    code="plugin_event_sequence_invalid",
                )
            self._last_public_child_sequence_by_run[item.run_id] = item.sequence
            return HarnessEvent(
                thread_id=item.thread_id,
                run_id=item.run_id,
                sequence=item.sequence,
                occurred_at=item.occurred_at,
                event=event,
            )
        return HarnessEvent(
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=self._next_public_sequence(),
            occurred_at=datetime.now(UTC),
            event=event,
        )

    async def _raise_after_failure(self, failure: BaseException) -> None:
        """Close a failed stream and retain an already validated inner outcome."""
        outcome = self._last_valid_outcome
        try:
            await self._close_resources(outcome=outcome)
        except RunCleanupError as cleanup_error:
            if outcome is None:
                raise cleanup_error from failure
            raise RunCleanupError(
                "Harness run failed after producing an outcome and cleanup also failed.",
                outcome=outcome,
                causes=(failure, *cleanup_error.causes),
            ) from failure
        if outcome is not None:
            raise RunCleanupError(
                "Harness run failed after producing an outcome.",
                outcome=outcome,
                causes=(failure,),
            ) from failure
        raise failure

    def cancel(self) -> None:
        """Request native Pydantic AI cancellation, including before Agent start."""
        if self._terminal_yielded or self._closed:
            return
        self._cancel_requested = True
        self._cancel_event.set()
        if self._pydantic_events is not None:
            self._pydantic_events.cancel()

    async def steer(self, input: RunInputValue) -> str:
        """Retain and deliver one user steering value through native Pydantic enqueue."""
        if not self._entered or self._closed or self._context is None:
            raise RunError("The run is not active.", code="run_not_active")
        return await self._context._steering.steer(input)

    async def export_state(self) -> HarnessState:
        """Export the latest complete message and Capability-state boundary."""
        if not self._entered or self._closed or self._context is None:
            raise StateError("The run is not active.", code="run_not_active")
        self._refresh_live_messages()
        return await self._context.export_state(self._latest_messages)

    def _build_response(
        self,
        plugins: tuple[AbstractHarnessPlugin, ...],
        index: int,
        exchange: PluginRunExchange,
    ) -> PluginRunResponse[OutputT]:
        if index == len(plugins):
            response = PluginRunResponse(self._agent_items(exchange))
        else:
            plugin = plugins[index]
            call_next = PluginRunNext[OutputT](
                lambda next_exchange: self._build_response(plugins, index + 1, next_exchange)
            )
            response = plugin.wrap_run(exchange, call_next)
            if not isinstance(response, PluginRunResponse):
                raise PluginError(
                    "Plugin wrap_run must return PluginRunResponse.",
                    code="plugin_response_invalid",
                    details={"plugin_id": plugin.plugin_id},
                )
        typed_response = cast(PluginRunResponse[OutputT], response)
        typed_response._bind_item_validator(self._validate_plugin_response_item)
        registered = self._register_response(typed_response, depth=index * 2 + 1)
        boundary = PluginRunResponse(self._response_boundary_items(registered, terminal=index == 0))
        boundary._bind_item_validator(self._validate_plugin_response_item)
        return self._register_response(boundary, depth=index * 2)

    def _validate_plugin_response_item(
        self,
        item: HarnessEvent | HarnessRunResult[OutputT],
    ) -> HarnessEvent | HarnessRunResult[OutputT]:
        if isinstance(item, HarnessRunResult):
            validated = self._validate_result_candidate(item)
            self._last_valid_outcome = validated
            return validated
        return item

    def _register_response(
        self,
        response: PluginRunResponse[OutputT],
        *,
        depth: int,
    ) -> PluginRunResponse[OutputT]:
        response_id = id(response)
        if response_id not in self._response_ids:
            self._response_ids.add(response_id)
            self._responses.append((depth, response))
        return response

    async def _response_boundary_items(
        self,
        response: PluginRunResponse[OutputT],
        *,
        terminal: bool,
    ) -> AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]]:
        async for item in response:
            if not isinstance(item, HarnessRunResult):
                yield item
                continue
            if terminal:
                self._install_terminal_fence()
                await self.context.usage_attribution._flush(reason="terminal")
                item = self._validate_result_candidate(item.replace(usage_records=self.context.usage_records))
            target_version = self.context.environment.topology.topology_version
            async for event in self._drain_emitter_through(target_version, terminal=terminal):
                yield event
            if terminal:
                self._emitter.close()
            yield item
            return

    async def _drain_emitter_through(
        self,
        target_version: int,
        *,
        terminal: bool,
    ) -> AsyncGenerator[HarnessEvent]:
        emitter_task: asyncio.Task[HarnessExtensionEvent | HarnessEvent] | None = None
        progress_task: asyncio.Task[bool] | None = None
        deadline_task = asyncio.create_task(asyncio.sleep(5.0)) if terminal else None
        try:
            while True:
                if emitter_task is not None and emitter_task.done():
                    event = emitter_task.result()
                    emitter_task = None
                    yield self._adapt_extension_event(event) if isinstance(event, HarnessExtensionEvent) else event
                    continue

                adapter_task = self._environment_event_task
                if adapter_task is not None and adapter_task.done():
                    self._environment_event_task = None
                    adapter_task.result()
                    adapter_task = None
                    if not terminal or not self._topology_event_drain.drained.is_set():
                        raise RunError(
                            "Environment topology event adapter stopped before the terminal journal fence.",
                            code="event_adapter_stopped",
                        )

                progress = self._topology_event_drain.progress
                cursor = self._topology_event_drain.cursor
                adapter_complete = adapter_task is None or adapter_task.done()
                terminal_complete = not terminal or (self._topology_event_drain.drained.is_set() and adapter_complete)
                if cursor is not None and cursor >= target_version and self._emitter.empty() and terminal_complete:
                    if emitter_task is not None and not emitter_task.done():
                        emitter_task.cancel()
                        await asyncio.gather(emitter_task, return_exceptions=True)
                    return

                if emitter_task is None:
                    emitter_task = asyncio.create_task(self._emitter.next())
                if progress_task is None:
                    progress_task = asyncio.create_task(progress.wait())
                wait_for: set[asyncio.Task[Any]] = {emitter_task, progress_task}
                if adapter_task is not None:
                    wait_for.add(adapter_task)
                if deadline_task is not None:
                    wait_for.add(deadline_task)
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
                if deadline_task is not None and deadline_task in done:
                    raise RunError(
                        "Environment topology event adapter did not drain before cleanup deadline.",
                        code="event_adapter_cleanup_timeout",
                    )
                if progress_task in done:
                    progress_task.result()
                    progress_task = None
        finally:
            tasks: list[asyncio.Task[Any]] = [
                task for task in (emitter_task, progress_task, deadline_task) if task is not None
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _pump_model_items(
        self,
        source: AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]],
        queue: asyncio.Queue[Any],
    ) -> None:
        error: BaseException | None = None
        cancellation: asyncio.CancelledError | None = None
        try:
            async for item in source:
                await queue.put(item)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except BaseException as exc:
            error = exc
        try:
            await source.aclose()
        except BaseException as exc:
            error = exc if error is None else BaseExceptionGroup("Harness model source cleanup failed", [error, exc])
        if cancellation is not None:
            if error is not None:
                cancellation.add_note(f"Harness model source cleanup also failed: {error!r}")
            raise cancellation
        await queue.put(_ResponsePumpTerminal(error=error))

    async def _agent_items(
        self,
        exchange: PluginRunExchange,
    ) -> AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]]:
        source_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
        source_pump = asyncio.create_task(self._pump_model_items(self._model_items(exchange), source_queue))
        source_task: asyncio.Task[Any] | None = None
        emitter_task: asyncio.Task[HarnessExtensionEvent | HarnessEvent] | None = None
        pending_result: HarnessRunResult[OutputT] | None = None
        try:
            while True:
                if emitter_task is not None and emitter_task.done():
                    event = emitter_task.result()
                    emitter_task = None
                    yield self._adapt_extension_event(event) if isinstance(event, HarnessExtensionEvent) else event
                    continue
                if source_task is None:
                    source_task = asyncio.create_task(source_queue.get())
                if emitter_task is None:
                    emitter_task = asyncio.create_task(self._emitter.next())

                wait_for: set[asyncio.Task[Any]] = {source_task, emitter_task}
                adapter_task = self._environment_event_task
                if adapter_task is not None and not adapter_task.done():
                    wait_for.add(adapter_task)
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

                if adapter_task is not None and adapter_task in done:
                    self._environment_event_task = None
                    adapter_task.result()
                    raise RunError(
                        "Environment topology event adapter stopped before the logical terminal fence.",
                        code="event_adapter_stopped",
                    )
                if emitter_task in done:
                    event = emitter_task.result()
                    emitter_task = None
                    yield self._adapt_extension_event(event) if isinstance(event, HarnessExtensionEvent) else event
                    continue
                if source_task in done:
                    task = source_task
                    source_task = None
                    item = task.result()
                    if isinstance(item, _ResponsePumpTerminal):
                        await source_pump
                        if item.error is not None:
                            raise item.error
                        if pending_result is None:
                            return
                        if emitter_task is not None:
                            if emitter_task.done():
                                event = emitter_task.result()
                                emitter_task = None
                                yield (
                                    self._adapt_extension_event(event)
                                    if isinstance(event, HarnessExtensionEvent)
                                    else event
                                )
                            else:
                                emitter_task.cancel()
                                await asyncio.gather(emitter_task, return_exceptions=True)
                                emitter_task = None
                        target_version = self.context.environment.topology.topology_version
                        async for event in self._drain_emitter_through(target_version, terminal=False):
                            yield event
                        yield pending_result
                        return
                    if pending_result is not None:
                        raise RunError(
                            "Harness model source emitted an item after its result candidate.",
                            code="agent_result_not_terminal",
                        )
                    if isinstance(item, HarnessRunResult):
                        pending_result = item
                        continue
                    yield item
        finally:
            tasks: list[asyncio.Task[Any]] = [
                task for task in (source_task, emitter_task, source_pump) if task is not None
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _model_items(
        self,
        exchange: PluginRunExchange,
    ) -> AsyncGenerator[HarnessEvent | HarnessRunResult[OutputT]]:
        if exchange.context is not self.context:
            raise PluginError(
                "Plugin middleware replaced the trusted run context.",
                code="plugin_context_replaced",
            )
        if not isinstance(exchange.input, SemanticRunInput):
            raise PluginError("Plugin middleware supplied an invalid input.", code="plugin_input_invalid")
        try:
            current_input = normalize_input(exchange.input.value)
        except HarnessError as exc:
            raise PluginError("Plugin middleware supplied an invalid input.", code="plugin_input_invalid") from exc

        await exchange.context._steering.prepare(current_input)

        policy = self._executable.definition.model_recovery
        max_attempts = policy.max_attempts if policy.enabled else 1
        attempt_index = 0
        current_history, _ = normalize_interrupted_history(self._previous_state.message_history)
        self._latest_messages = current_history

        while True:
            retry_error: BaseException | None = None
            next_attempt_index = attempt_index + 1
            response_tracker = InterruptedResponseTracker()
            manager = self._executable._agent.run_stream_events(
                current_input.value,
                message_history=current_history,
                deferred_tool_results=(
                    self._deferred_resume.results if attempt_index == 0 and self._deferred_resume is not None else None
                ),
                run_id=f"model-attempt-{uuid4().hex}",
                deps=self.context,
                usage=self._usage,
                usage_limits=self._usage_limits,
                capabilities=self._bindings.capabilities,
            )
            try:
                async with manager as events:
                    self._pydantic_events = events
                    if self._cancel_requested:
                        events.cancel()
                    try:
                        async for event in self._merge_agent_events(events, response_tracker):
                            self._refresh_live_messages()
                            if isinstance(event, HarnessEvent):
                                yield event
                                continue
                            if isinstance(event, HarnessExtensionEvent):
                                yield self._adapt_extension_event(event)
                                continue
                            if isinstance(event, AgentRunResultEvent):
                                result = event.result
                                messages = tuple(result.all_messages())
                                new_message_index = len(messages) - len(result.new_messages())
                                self._latest_messages = messages
                                state = await exchange.context.export_state(messages)
                                if isinstance(result.output, DeferredToolRequests):
                                    deferred = bind_managed_approval_identities(
                                        result.output,
                                        exchange.context._managed_tool_ids,
                                    )
                                    candidate = HarnessRunResult(
                                        thread_id=self.thread_id,
                                        run_id=self.run_id,
                                        status="suspended",
                                        output=None,
                                        deferred=deferred,
                                        suspend_reason="deferred",
                                        state=state,
                                        usage=result.usage,
                                        _messages=messages,
                                        _new_message_index=new_message_index,
                                    )
                                else:
                                    candidate = HarnessRunResult(
                                        thread_id=self.thread_id,
                                        run_id=self.run_id,
                                        status="completed",
                                        output=result.output,
                                        state=state,
                                        usage=result.usage,
                                        _messages=messages,
                                        _new_message_index=new_message_index,
                                    )
                                yield self._record_inner_candidate(candidate)
                                return
                            yield self._adapt_event(cast(AgentStreamEvent, event))
                    except RunCancelled as exc:
                        raw_messages = exc.all_messages()
                        raw_new_message_count = len(exc.new_messages())
                        messages, _ = normalize_interrupted_history(
                            raw_messages,
                            response_tracker=response_tracker,
                        )
                        self._pydantic_events = None
                        self._latest_messages = messages
                        state = await exchange.context.export_state(messages) if exc.run_id is not None else None
                        yield self._record_inner_candidate(
                            HarnessRunResult(
                                thread_id=self.thread_id,
                                run_id=self.run_id,
                                status="cancelled",
                                output=None,
                                state=state,
                                usage=self._usage if exc.run_id is None else exc.usage,
                                _messages=messages,
                                _new_message_index=max(0, len(messages) - raw_new_message_count),
                            )
                        )
                        return
                    except UsageLimitExceeded:
                        self._refresh_live_messages()
                        messages, _ = normalize_interrupted_history(
                            self._latest_messages,
                            response_tracker=response_tracker,
                        )
                        self._pydantic_events = None
                        self._latest_messages = messages
                        yield await self._failed_candidate(
                            code="usage_limit_exceeded",
                            message="Pydantic AI usage limit exceeded.",
                            refresh_messages=False,
                        )
                        return
                    except Exception as error:
                        self._refresh_live_messages()
                        messages, _ = normalize_interrupted_history(
                            self._latest_messages,
                            response_tracker=response_tracker,
                        )
                        self._pydantic_events = None
                        self._latest_messages = messages
                        if self._cancel_requested:
                            state = await exchange.context.export_state(messages) if messages else None
                            yield self._record_inner_candidate(
                                HarnessRunResult(
                                    thread_id=self.thread_id,
                                    run_id=self.run_id,
                                    status="cancelled",
                                    output=None,
                                    state=state,
                                    usage=self._current_usage(),
                                    _messages=messages,
                                    _new_message_index=min(self._new_message_index, len(messages)),
                                )
                            )
                            return

                        model_failure = is_recoverable_model_failure(error, messages)
                        retryable = policy.enabled and model_failure
                        if retryable and next_attempt_index < max_attempts:
                            retry_error = error
                        elif model_failure or isinstance(error, AgentRunError):
                            exhausted = retryable
                            yield await self._failed_candidate(
                                code="model_recovery_exhausted" if exhausted else "agent_run_failed",
                                message=(
                                    "Model recovery attempts were exhausted."
                                    if exhausted
                                    else "Pydantic AI agent execution failed."
                                ),
                                refresh_messages=False,
                            )
                            return
                        else:
                            raise
            finally:
                self._pydantic_events = None

            assert retry_error is not None
            delay = policy.delay(next_attempt_index)
            if delay > 0:
                try:
                    await asyncio.wait_for(self._cancel_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
            if self._cancel_requested:
                state = await exchange.context.export_state(self._latest_messages) if self._latest_messages else None
                yield self._record_inner_candidate(
                    HarnessRunResult(
                        thread_id=self.thread_id,
                        run_id=self.run_id,
                        status="cancelled",
                        output=None,
                        state=state,
                        usage=self._usage,
                        _messages=self._latest_messages,
                        _new_message_index=min(self._new_message_index, len(self._latest_messages)),
                    )
                )
                return
            retry_input = await policy.build_prompt(retry_error, next_attempt_index, self._latest_messages)
            current_input = normalize_input(retry_input)
            current_history = self._latest_messages
            attempt_index = next_attempt_index

    async def _failed_candidate(
        self,
        *,
        code: str,
        message: str,
        refresh_messages: bool = True,
    ) -> HarnessRunResult[OutputT]:
        if refresh_messages:
            self._refresh_live_messages()
        state = await self.context.export_state(self._latest_messages)
        return self._record_inner_candidate(
            HarnessRunResult(
                thread_id=self.thread_id,
                run_id=self.run_id,
                status="failed",
                output=None,
                state=state,
                usage=self._current_usage(),
                failure=SafeFailure(
                    code=code,
                    message=message,
                    retry_hint="dependency_change",
                ),
                _messages=self._latest_messages,
                _new_message_index=min(self._new_message_index, len(self._latest_messages)),
            )
        )

    def _record_inner_candidate(
        self,
        candidate: HarnessRunResult[OutputT],
    ) -> HarnessRunResult[OutputT]:
        attributed = candidate.replace(usage_records=self.context.usage_records)
        validated = self._validate_result_candidate(attributed)
        self._last_valid_outcome = validated
        return validated

    def _validate_result_candidate(
        self,
        candidate: HarnessRunResult[OutputT],
    ) -> HarnessRunResult[OutputT]:
        if candidate.thread_id != self.thread_id:
            raise PluginError(
                "Plugin result thread_id does not match the active Thread.",
                code="plugin_result_thread_mismatch",
            )
        if candidate.run_id != self.run_id:
            raise PluginError(
                "Plugin result run_id does not match the active run.",
                code="plugin_result_run_mismatch",
            )
        try:
            messages = candidate.all_messages()
            new_messages = candidate.new_messages()
            new_message_index = len(messages) - len(new_messages)
            if new_message_index < 0 or messages[new_message_index:] != new_messages:
                raise ValueError("new messages are not a suffix of all messages")
            validated = HarnessRunResult(
                thread_id=candidate.thread_id,
                run_id=candidate.run_id,
                status=candidate.status,
                output=candidate.output,
                state=candidate.state,
                usage=candidate.usage,
                usage_records=candidate.usage_records,
                failure=candidate.failure,
                suspend_reason=candidate.suspend_reason,
                deferred=candidate.deferred,
                _messages=messages,
                _new_message_index=new_message_index,
            )
            if validated.status == "completed":
                output = validated.output
                if _business_output_contains_deferred_value(output):
                    raise ValueError("deferred output must suspend the run")
                self._executable._output_adapter.validate_python(output, strict=True)
            return validated
        except (TypeError, ValueError, ValidationError) as exc:
            raise PluginError(
                "Plugin emitted an invalid result candidate.",
                code="plugin_result_invalid",
            ) from exc

    def _adapt_event(self, event: AgentStreamEvent) -> HarnessEvent:
        envelope = HarnessEvent(
            thread_id=self.thread_id,
            run_id=self.run_id,
            sequence=self._source_sequence,
            occurred_at=datetime.now(UTC),
            event=event,
        )
        self._source_sequence += 1
        return envelope

    def _adapt_extension_event(self, event: HarnessExtensionEvent) -> HarnessEvent:
        envelope = self._emitter.envelope(event, sequence=self._source_sequence)
        self._source_sequence += 1
        return envelope

    async def _merge_agent_events(
        self,
        events: AgentRunEvents[OutputT | DeferredToolRequests],
        response_tracker: InterruptedResponseTracker,
    ) -> AsyncIterator[Any]:
        async for event in events:
            response_tracker.observe(
                cast(AgentStreamEvent, event),
                response_history_count=len(events.all_messages()),
            )
            yield event

    def _next_public_sequence(self) -> int:
        sequence = self._public_sequence
        self._public_sequence += 1
        return sequence

    def _refresh_live_messages(self) -> None:
        events = self._pydantic_events
        if events is None:
            return
        try:
            self._latest_messages = tuple(events.all_messages())
        except UserError:
            # Before Pydantic binds the run, imported history remains the latest complete boundary.
            return

    def _current_usage(self) -> RunUsage:
        events = self._pydantic_events
        if events is None:
            return self._usage
        try:
            return events.usage
        except UserError:
            return self._usage

    async def _close_resources(
        self,
        *,
        outcome: HarnessRunResult[Any] | None,
        cancellation: asyncio.CancelledError | None = None,
    ) -> None:
        if self._closed:
            if cancellation is not None:
                raise cancellation
            return

        fence_failure: BaseException | None = None
        try:
            self._bindings.environment.controller.begin_close()
        except BaseException as exc:
            fence_failure = exc

        current_task = asyncio.current_task()
        causes: list[BaseException] = [] if fence_failure is None else [fence_failure]

        def capture_pending_cancellation(exc: asyncio.CancelledError | None = None) -> bool:
            nonlocal cancellation
            if current_task is None or not current_task.cancelling():
                return False
            if cancellation is None:
                cancellation = exc or asyncio.CancelledError()
            while current_task.cancelling():
                current_task.uncancel()
            return True

        capture_pending_cancellation(cancellation)

        async def finish_cleanup(awaitable: Awaitable[None]) -> None:
            try:
                # Cleanup normally stays in the task that entered plugin and AnyIO scopes.
                await awaitable
            except asyncio.CancelledError as exc:
                if not capture_pending_cancellation(exc):
                    causes.append(exc)
            except BaseException as exc:
                causes.append(exc)
            finally:
                # Cleanup code may suppress or translate the injected CancelledError.
                capture_pending_cancellation()

        await finish_cleanup(self._cancel_logical_source_tasks())
        await finish_cleanup(self._close_registered_responses())
        await finish_cleanup(self._close_run_attachments())
        causes.extend(self._source_cleanup_failures)
        self._source_cleanup_failures.clear()

        await finish_cleanup(self._close_environment_lifecycle())
        self._emitter.close()
        if self._environment_event_task is not None:
            task = self._environment_event_task
            self._environment_event_task = None
            await finish_cleanup(_stop_environment_event_task(task))
        self._closed = True

        if cancellation is not None:
            for cause in causes:
                cancellation.add_note(f"Harness cleanup also failed: {cause!r}")
            raise cancellation
        if causes:
            raise RunCleanupError(
                "Harness run cleanup failed.",
                outcome=outcome,
                causes=tuple(causes),
            )

    async def _close_run_attachments(self) -> None:
        """Close collaborators claimed by finalized definition owners before Environment teardown."""
        if self._run_attachments_closed:
            return
        self._run_attachments_closed = True
        if self._context is not None:
            await self._context._close_run_cleanups()


def _resolve_business_output[OutputT](
    definition: AgentDefinition[OutputT],
) -> tuple[AgentSpec, Any, TypeAdapter[Any]]:
    """Resolve and freeze the one business-output source used for Agent construction."""
    construction_spec = definition.agent.model_copy(deep=True)
    if definition.output_type is not None:
        business_output = _prepare_pydantic_output_spec(definition.output_type)
        return construction_spec, business_output, _build_output_adapter(definition.output_type)

    schema = construction_spec.output_schema
    assert schema is not None
    try:
        business_output = StructuredDict(deepcopy(schema))
        output_adapter = TypeAdapter(business_output)
    except Exception as exc:
        raise DefinitionError(
            "AgentSpec.output_schema is not a valid native structured object schema.",
            code="agent_build_failed",
            details={"definition_id": definition.definition_id},
        ) from exc
    construction_spec = construction_spec.model_copy(update={"output_schema": None}, deep=True)
    return construction_spec, business_output, output_adapter


def _validate_built_capability_tree(
    root: AbstractCapability[AgentContext],
    *,
    definition_reserved_ids: frozenset[str],
) -> None:
    """Validate stable IDs and protected provenance on the complete Agent-bound tree."""
    leaves: list[AbstractCapability[AgentContext]] = []
    root.apply(leaves.append)
    seen_ids: dict[str, str] = {}
    execution_boundary_count = 0
    tool_surface_count = 0
    message_integrity_count = 0
    lifecycle_event_count = 0
    steering_count = 0
    model_context_coordinator_count = 0
    usage_count = 0
    reserved_ids = {
        TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID,
        TOOL_SURFACE_CAPABILITY_ID,
        MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID,
        LIFECYCLE_EVENT_CAPABILITY_ID,
        STEERING_CAPABILITY_ID,
        MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID,
        INVOCATION_POLICY_CAPABILITY_ID,
        USAGE_CAPABILITY_ID,
        MODEL_COST_RUN_CAPABILITY_ID,
        CLIENT_TOOLS_CAPABILITY_ID,
        CLIENT_TOOLS_RUN_CAPABILITY_ID,
        CODEACT_CAPABILITY_ID,
        DYNAMIC_ENVIRONMENT_CAPABILITY_ID,
        RUNTIME_CONTEXT_CAPABILITY_ID,
        WORKSPACE_OUTLINE_CAPABILITY_ID,
        FILE_CONTEXT_CAPABILITY_ID,
        HANDOFF_CAPABILITY_ID,
        COMPACTION_CAPABILITY_ID,
        MONITORED_PROCESS_CAPABILITY_ID,
        MONITORED_PROCESS_RUN_CAPABILITY_ID,
        USER_INTERACTION_CAPABILITY_ID,
        SKILLS_CAPABILITY_ID,
        SKILL_SELECTION_RUN_CAPABILITY_ID,
        MEDIA_CAPABILITY_ID,
        MEDIA_RUN_CAPABILITY_ID,
        DOCUMENTS_CAPABILITY_ID,
        DOCUMENTS_RUN_CAPABILITY_ID,
        WEB_CAPABILITY_ID,
        WEB_RUN_CAPABILITY_ID,
        WORKING_STATE_CAPABILITY_ID,
        TASK_STATE_RUN_CAPABILITY_ID,
        DELEGATION_CAPABILITY_ID,
        DELEGATION_RUN_CAPABILITY_ID,
    }
    for capability in leaves:
        if not isinstance(capability, AbstractCapability):
            raise DefinitionError(
                "Built Capability trees must contain only AbstractCapability leaves.",
                code="capability_type_invalid",
                details={"source": "built"},
            )
        capability_id = capability.id
        if capability_id is not None:
            if not isinstance(capability_id, str) or not capability_id.strip():
                raise DefinitionError(
                    "Capability IDs must be non-blank strings when present.",
                    code="capability_id_invalid",
                    details={"capability_type": type(capability).__name__},
                )
            previous = seen_ids.get(capability_id)
            if previous is not None:
                raise DefinitionError(
                    "Capability IDs must be unique in the built Agent tree.",
                    code="capability_id_duplicate",
                    details={
                        "capability_id": capability_id,
                        "capability_type": type(capability).__name__,
                        "other_capability_type": previous,
                    },
                )
            seen_ids[capability_id] = type(capability).__name__

        if isinstance(capability, ToolExecutionBoundaryCapability):
            execution_boundary_count += 1
            if capability_id != TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID:
                raise DefinitionError(
                    "The mandatory tool execution boundary Capability has an invalid ID.",
                    code="capability_scope_invalid",
                )
            continue
        if isinstance(capability, ToolSurfaceCapability):
            tool_surface_count += 1
            if capability_id != TOOL_SURFACE_CAPABILITY_ID:
                raise DefinitionError(
                    "The mandatory tool-surface Capability has an invalid ID.",
                    code="capability_scope_invalid",
                )
            continue
        if type(capability) is MessageIntegrityFilterCapability:
            message_integrity_count += 1
            if capability_id != MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID:
                raise DefinitionError(
                    "The mandatory message-integrity Filter Capability has an invalid ID.",
                    code="capability_scope_invalid",
                )
            continue
        if type(capability) is LifecycleEventCapability:
            lifecycle_event_count += 1
            if capability_id != LIFECYCLE_EVENT_CAPABILITY_ID:
                raise DefinitionError(
                    "The mandatory lifecycle event Capability has an invalid ID.",
                    code="capability_scope_invalid",
                )
            continue
        if type(capability) is SteeringCapability:
            steering_count += 1
            if capability_id != STEERING_CAPABILITY_ID:
                raise DefinitionError(
                    "The mandatory steering Capability has an invalid ID.",
                    code="capability_scope_invalid",
                )
            continue
        if type(capability) is ModelContextCoordinatorCapability:
            model_context_coordinator_count += 1
            if capability_id != MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID:
                raise DefinitionError(
                    "The mandatory model context coordinator Capability has an invalid ID.",
                    code="capability_scope_invalid",
                )
            continue
        if type(capability) is UsageCapability:
            usage_count += 1
            if capability_id != USAGE_CAPABILITY_ID:
                raise DefinitionError(
                    "The mandatory Usage Capability has an invalid ID.",
                    code="capability_scope_invalid",
                )
            continue

        allowed_definition_reserved = (
            type(capability)
            in (
                ClientToolsCapability,
                CodeActCapability,
                DynamicEnvironmentCapability,
                RuntimeContextCapability,
                WorkspaceOutlineCapability,
                FileContextCapability,
                HandoffCapability,
                CompactionCapability,
                MonitoredProcessCapability,
                UserInteractionCapability,
                SkillsCapability,
                MediaCapability,
                DocumentsCapability,
                WebCapability,
                WorkingStateCapability,
                DelegationCapability,
            )
            and capability_id in definition_reserved_ids
        )
        if (
            isinstance(capability, InvocationPolicyCapability | ClientToolsRunCapability)
            or capability_id in reserved_ids
        ) and not allowed_definition_reserved:
            raise DefinitionError(
                "A reserved Harness Capability is present in the built Agent tree from the wrong source.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability_id,
                    "capability_type": type(capability).__name__,
                    "source": "built",
                },
            )

    surface_index = next(
        (index for index, capability in enumerate(leaves) if isinstance(capability, ToolSurfaceCapability)),
        None,
    )
    if surface_index is not None:
        for capability in leaves[:surface_index]:
            if isinstance(capability, ToolExecutionBoundaryCapability | CodeActCapability):
                continue
            if type(capability).get_wrapper_toolset is not AbstractCapability.get_wrapper_toolset:
                raise DefinitionError(
                    "Only CodeAct and the tool execution boundary may wrap the mandatory tool surface.",
                    code="tool_surface_order_invalid",
                    details={"capability_type": type(capability).__name__},
                )

    if execution_boundary_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory tool execution boundary Capability.",
            code="capability_scope_invalid",
        )
    if tool_surface_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory tool-surface Capability.",
            code="capability_scope_invalid",
        )
    if message_integrity_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory message-integrity Filter Capability.",
            code="capability_scope_invalid",
        )
    if lifecycle_event_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory lifecycle event Capability.",
            code="capability_scope_invalid",
        )
    if steering_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory steering Capability.",
            code="capability_scope_invalid",
        )
    if model_context_coordinator_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory model context coordinator Capability.",
            code="capability_scope_invalid",
        )
    if usage_count != 1:
        raise DefinitionError(
            "The built Agent must contain exactly one mandatory Usage Capability.",
            code="capability_scope_invalid",
        )


def _capture_skill_selection_names(
    capabilities: Sequence[AbstractCapability[AgentContext]],
) -> frozenset[str] | None:
    selections = tuple(capability for capability in capabilities if type(capability) is SkillSelectionRunCapability)
    if len(selections) > 1:
        raise DefinitionError(
            "RunBindings contains duplicate Host skill selections.",
            code="capability_id_duplicate",
            details={"capability_id": SKILL_SELECTION_RUN_CAPABILITY_ID, "source": "run"},
        )
    return frozenset(selections[0].names) if selections else None


def _validate_capability_source(
    capabilities: Sequence[AbstractCapability[AgentContext]],
    *,
    source: Literal["definition", "run"],
) -> frozenset[str]:
    """Flatten Capability trees and preserve ownership of reserved Harness IDs."""
    run_types = (
        InvocationPolicyCapability,
        ClientToolsRunCapability,
        MonitoredProcessRunCapability,
        SkillSelectionRunCapability,
        MediaRunCapability,
        DocumentsRunCapability,
        WebRunCapability,
        TaskStateRunCapability,
        ModelCostRunCapability,
        DelegationRunCapability,
    )
    leaves: list[AbstractCapability[AgentContext]] = []
    for capability in capabilities:
        if not isinstance(capability, AbstractCapability):
            raise DefinitionError(
                "Configured Capabilities must inherit AbstractCapability.",
                code="capability_type_invalid",
                details={"source": source},
            )
        if source == "run" and type(capability) not in run_types:
            raise DefinitionError(
                "RunBindings accepts only exact documented run attachment Capability types.",
                code="capability_scope_invalid",
                details={
                    "capability_id": capability.id,
                    "capability_type": type(capability).__name__,
                    "source": source,
                },
            )
        capability.apply(leaves.append)

    reserved_ids = {
        TOOL_EXECUTION_BOUNDARY_CAPABILITY_ID,
        TOOL_SURFACE_CAPABILITY_ID,
        MESSAGE_INTEGRITY_FILTER_CAPABILITY_ID,
        STEERING_CAPABILITY_ID,
        MODEL_CONTEXT_COORDINATOR_CAPABILITY_ID,
        INVOCATION_POLICY_CAPABILITY_ID,
        USAGE_CAPABILITY_ID,
        MODEL_COST_RUN_CAPABILITY_ID,
        CLIENT_TOOLS_CAPABILITY_ID,
        CLIENT_TOOLS_RUN_CAPABILITY_ID,
        CODEACT_CAPABILITY_ID,
        DYNAMIC_ENVIRONMENT_CAPABILITY_ID,
        RUNTIME_CONTEXT_CAPABILITY_ID,
        WORKSPACE_OUTLINE_CAPABILITY_ID,
        FILE_CONTEXT_CAPABILITY_ID,
        HANDOFF_CAPABILITY_ID,
        COMPACTION_CAPABILITY_ID,
        MONITORED_PROCESS_CAPABILITY_ID,
        MONITORED_PROCESS_RUN_CAPABILITY_ID,
        USER_INTERACTION_CAPABILITY_ID,
        SKILLS_CAPABILITY_ID,
        SKILL_SELECTION_RUN_CAPABILITY_ID,
        MEDIA_CAPABILITY_ID,
        MEDIA_RUN_CAPABILITY_ID,
        DOCUMENTS_CAPABILITY_ID,
        DOCUMENTS_RUN_CAPABILITY_ID,
        WEB_CAPABILITY_ID,
        WEB_RUN_CAPABILITY_ID,
        WORKING_STATE_CAPABILITY_ID,
        TASK_STATE_RUN_CAPABILITY_ID,
        DELEGATION_CAPABILITY_ID,
        DELEGATION_RUN_CAPABILITY_ID,
    }
    accepted: set[str] = set()
    for capability in leaves:
        if not isinstance(capability, AbstractCapability):
            raise DefinitionError(
                "Capability trees must contain only AbstractCapability leaves.",
                code="capability_type_invalid",
                details={"source": source},
            )
        allowed = (
            source == "definition"
            and type(capability)
            in (
                ClientToolsCapability,
                CodeActCapability,
                DynamicEnvironmentCapability,
                RuntimeContextCapability,
                WorkspaceOutlineCapability,
                FileContextCapability,
                HandoffCapability,
                CompactionCapability,
                MonitoredProcessCapability,
                UserInteractionCapability,
                SkillsCapability,
                MediaCapability,
                DocumentsCapability,
                WebCapability,
                WorkingStateCapability,
                DelegationCapability,
            )
        ) or (source == "run" and type(capability) in run_types)
        reserved_type = isinstance(
            capability,
            ToolExecutionBoundaryCapability
            | ToolSurfaceCapability
            | MessageIntegrityFilterCapability
            | SteeringCapability
            | ModelContextCoordinatorCapability
            | InvocationPolicyCapability
            | UsageCapability
            | ModelCostRunCapability
            | ClientToolsCapability
            | ClientToolsRunCapability
            | CodeActCapability
            | DynamicEnvironmentCapability
            | RuntimeContextCapability
            | WorkspaceOutlineCapability
            | FileContextCapability
            | HandoffCapability
            | CompactionCapability
            | MonitoredProcessCapability
            | MonitoredProcessRunCapability
            | UserInteractionCapability
            | SkillsCapability
            | SkillSelectionRunCapability
            | MediaCapability
            | MediaRunCapability
            | DocumentsCapability
            | DocumentsRunCapability
            | WebCapability
            | WebRunCapability
            | WorkingStateCapability
            | TaskStateRunCapability
            | DelegationCapability
            | DelegationRunCapability,
        )
        if reserved_type or capability.id in reserved_ids:
            if not allowed:
                raise DefinitionError(
                    "A reserved Harness Capability is installed from the wrong source.",
                    code="capability_scope_invalid",
                    details={
                        "capability_id": capability.id,
                        "capability_type": type(capability).__name__,
                        "source": source,
                    },
                )
            if capability.id is not None:
                accepted.add(capability.id)
    return frozenset(accepted)


def _prepare_pydantic_output_spec(value: Any) -> Any:
    """Give Pydantic semantic return annotations for supported sync-awaitable output functions."""
    if isinstance(value, TextOutput):
        output_function = _prepare_pydantic_output_callable(value.output_function)
        return value if output_function is value.output_function else TextOutput(output_function)
    if isinstance(value, ToolOutput):
        output = _prepare_pydantic_output_spec(value.output)
        if output is value.output:
            return value
        return ToolOutput(
            output,
            name=value.name,
            description=value.description,
            max_retries=value.max_retries,
            strict=value.strict,
            sequential=value.sequential,
        )
    if isinstance(value, NativeOutput):
        outputs = _prepare_pydantic_output_spec(value.outputs)
        if outputs is value.outputs:
            return value
        return NativeOutput(
            outputs,
            name=value.name,
            description=value.description,
            strict=value.strict,
            template=value.template,
        )
    if isinstance(value, PromptedOutput):
        outputs = _prepare_pydantic_output_spec(value.outputs)
        if outputs is value.outputs:
            return value
        return PromptedOutput(
            outputs,
            name=value.name,
            description=value.description,
            template=value.template,
        )
    if isinstance(value, tuple):
        prepared = tuple(_prepare_pydantic_output_spec(item) for item in value)
        return (
            value if all(current is original for current, original in zip(prepared, value, strict=True)) else prepared
        )
    if isinstance(value, list):
        prepared = [_prepare_pydantic_output_spec(item) for item in value]
        return (
            value if all(current is original for current, original in zip(prepared, value, strict=True)) else prepared
        )
    if inspect.isfunction(value) or inspect.ismethod(value):
        return _prepare_pydantic_output_callable(value)
    return value


def _prepare_pydantic_output_callable(function: Callable[..., Any]) -> Callable[..., Any]:
    type_hints = get_type_hints(function, include_extras=True)
    return_type = type_hints.get("return", Any)
    semantic_return_type = _semantic_output_return_type(function, return_type)
    if semantic_return_type == return_type:
        return function

    def output_facade(*args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    output_facade.__name__ = function.__name__
    output_facade.__qualname__ = function.__qualname__
    output_facade.__module__ = function.__module__
    output_facade.__doc__ = function.__doc__
    output_facade.__annotations__ = {**type_hints, "return": semantic_return_type}
    signature = inspect.signature(function).replace(return_annotation=semantic_return_type)
    cast(Any, output_facade).__signature__ = signature
    return output_facade


def _semantic_output_return_type(function: Callable[..., Any], return_type: Any) -> Any:
    if inspect.iscoroutinefunction(function):
        return return_type
    candidate = return_type
    while get_origin(candidate) is typing.Annotated:
        arguments = get_args(candidate)
        candidate = arguments[0] if arguments else Any
    origin = get_origin(candidate)
    if origin is Awaitable:
        arguments = get_args(candidate)
        return arguments[0] if arguments else Any
    if origin is Coroutine:
        arguments = get_args(candidate)
        return arguments[2] if len(arguments) == 3 else Any
    return return_type


def _business_output_contains_deferred_value(value: Any) -> bool:
    """Fail closed if reserved suspension control leaks into a completed container value."""
    seen: set[int] = set()

    def contains(item: Any) -> bool:
        if isinstance(item, DeferredToolRequests):
            return True
        if isinstance(item, dict):
            nested = list(item.values())
        elif isinstance(item, BaseModel):
            nested = [getattr(item, field_name) for field_name in type(item).model_fields]
            if item.model_extra is not None:
                nested.extend(item.model_extra.values())
        elif not isinstance(item, type) and is_dataclass(item):
            nested = [getattr(item, field.name) for field in dataclass_fields(cast(Any, item))]
        elif isinstance(item, list | tuple | set | frozenset):
            nested = list(item)
        else:
            return False
        item_id = id(item)
        if item_id in seen:
            return False
        seen.add(item_id)
        return any(contains(nested_value) for nested_value in nested)

    return contains(value)


def _output_spec_contains_deferred_requests(output_spec: OutputSpec[Any]) -> bool:
    """Return whether a business output spec directly or transitively reserves deferred control output."""
    seen: set[int] = set()

    def contains(value: Any) -> bool:
        value_id = id(value)
        if value_id in seen:
            return False
        seen.add(value_id)

        if isinstance(value, type) and issubclass(value, DeferredToolRequests):
            return True
        if isinstance(value, type) and issubclass(value, BaseModel):
            return any(contains(model_field.annotation) for model_field in value.model_fields.values())
        if isinstance(value, type) and is_dataclass(value):
            annotations = get_type_hints(value, include_extras=True)
            return any(contains(annotations.get(item.name, item.type)) for item in dataclass_fields(value))
        if isinstance(value, type) and is_typeddict(value):
            return any(contains(annotation) for annotation in get_type_hints(value, include_extras=True).values())
        if isinstance(value, typing.TypeAliasType):
            return contains(value.__value__)
        if get_origin(value) is typing.Annotated:
            arguments = get_args(value)
            return bool(arguments) and contains(arguments[0])
        if isinstance(value, NativeOutput | PromptedOutput):
            return contains(value.outputs)
        if isinstance(value, ToolOutput):
            return contains(value.output)
        if isinstance(value, TextOutput):
            return contains_callable(value.output_function)
        if isinstance(value, Sequence):
            return any(contains(item) for item in value)
        origin = get_origin(value)
        if origin in (typing.Union, type(str | int), typing.Required, typing.NotRequired):
            return any(contains(item) for item in get_args(value))
        if isinstance(origin, type):
            structured_origin = issubclass(origin, BaseModel) or is_dataclass(origin) or is_typeddict(origin)
            if structured_origin or issubclass(origin, Collection):
                return any(item is not Ellipsis and contains(item) for item in get_args(value))
        if inspect.isfunction(value) or inspect.ismethod(value):
            return contains_callable(value)
        return False

    def contains_callable(function: Callable[..., Any]) -> bool:
        return_type = get_type_hints(function, include_extras=True).get("return", Any)
        return contains(_semantic_output_return_type(function, return_type))

    return contains(output_spec)


def _build_output_adapter(output_spec: OutputSpec[Any]) -> TypeAdapter[Any]:
    """Build a validator for the semantic value returned by an output specification."""
    output_types: list[Any] = []

    def collect(value: Any) -> None:
        if isinstance(value, NativeOutput | PromptedOutput):
            collect(value.outputs)
        elif isinstance(value, ToolOutput):
            collect(value.output)
        elif isinstance(value, TextOutput):
            collect_callable(value.output_function)
        elif isinstance(value, Sequence):
            for item in value:
                collect(item)
        elif get_origin(value) in (typing.Union, type(str | int)):
            for item in get_args(value):
                collect(item)
        elif inspect.isfunction(value) or inspect.ismethod(value):
            collect_callable(value)
        elif value is None:
            output_types.append(type(None))
        else:
            output_types.append(value)

    def collect_callable(function: Callable[..., Any]) -> None:
        return_type = get_type_hints(function, include_extras=True).get("return", Any)
        collect(_semantic_output_return_type(function, return_type))

    collect(output_spec)
    if not output_types:
        raise ValueError("Output specifications must contain at least one semantic output type.")
    validation_type = reduce(or_, output_types)
    try:
        return TypeAdapter(validation_type)
    except PydanticSchemaGenerationError:
        return TypeAdapter(validation_type, config=ConfigDict(arbitrary_types_allowed=True))
