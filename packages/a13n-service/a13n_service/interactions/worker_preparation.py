"""Reconstruct the accepted Agent and its input without retaining database sessions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from functools import partial
from typing import Any

from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
    EnvironmentAccess,
    EnvironmentMount,
    RunBindings,
    RunInputValue,
    RunPreparationContext,
)
from a13n_harness.capabilities import SubagentCapability, UserInteractionCapability, WebBinding
from a13n_harness.errors import RunError
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from anyio import to_thread
from pydantic import TypeAdapter
from pydantic_ai import ToolDenied
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agent_configuration.knowledge import KnowledgeFiles, knowledge_capability
from a13n_service.agent_configuration.runtime import ConfigurationCapability, validate_configuration_definition
from a13n_service.agents.execution_graph import inline_child_executions
from a13n_service.agents.plugin_preparation import prepare_agent_plugins
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext, AgentReconstructor
from a13n_service.agents.toolsets import web_selection
from a13n_service.assets.runtime import AssetRuntime
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.mount_domain import AcceptedRunMount
from a13n_service.environments.mount_observations import RunMountObservations
from a13n_service.environments.mount_runtime import RunMountRuntime
from a13n_service.environments.runtime import prepare_run_environment, validate_run_environment
from a13n_service.environments.websocket.worker_connections import WorkerClientConnections
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.runtime import SnapshotRunModelResolver
from a13n_service.observability import observe_input
from a13n_service.secrets.agent_inputs import graph_secret_requirements
from a13n_service.secrets.agent_runtime import AgentSecretRuntime, BoundAgentSecrets
from a13n_service.skills.runtime import PreparedSkillRuntime, SkillRuntimePreparer
from a13n_service.storage import short_session
from a13n_service.subagents.result_delivery import AsyncSubagentResultMaterializer
from a13n_service.web.runtime import WebRuntime, graph_uses_web

from .agent_resources import prepare_agent_resources, validate_agent_resources
from .attempt_resources import attempt_resource_stack
from .attempts import AttemptContext
from .control_domain import WaitingRunContinueInput, WaitingRunFeedback
from .control_models import ThreadInboxRecord
from .domain import Run, RunInputKind
from .harness_control import InlineRunControlCapability
from .harness_results import AttemptCommitter
from .harness_runtime import (
    HarnessCollaborators,
    HarnessInvocation,
    ImmediateHarnessInput,
    MaterializedHarnessInput,
    MountedHarnessEnvironments,
)
from .input import AcceptedAgentInput
from .objects import RunPayloadStore
from .ports.memory import ActiveMemory, ExecutionMemoryRuntime, PreparedMemory
from .protocol_context import ProtocolContextCapability
from .run_control import RunAttemptControl
from .state import CompletedOutcomeCandidate
from .worker_input import WorkerInputCapability, WorkerInputMaterializer, WorkerInputSources


class WorkerAttemptPreparer:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        run: Run,
        workspace_id: str,
        catalog: HarnessPluginFactoryCatalog,
        control: RunAttemptControl,
        committer: AttemptCommitter,
        payloads: RunPayloadStore,
        sources: WorkerInputSources,
        inputs: WorkerInputMaterializer,
        model_resolver: LiveProviderResolver,
        model_factory: NativeModelFactory,
        skills: SkillRuntimePreparer,
        asset_publication: AssetRuntime,
        async_results: AsyncSubagentResultMaterializer,
        environments: EnvironmentLifecycle,
        external_tools: ExternalToolRuntime,
        subagent_capability: Callable[[], SubagentCapability],
        secrets: AgentSecretRuntime | None = None,
        web: WebRuntime | None = None,
        memory: ExecutionMemoryRuntime | None = None,
        configuration_capability: Callable[[], ConfigurationCapability] | None = None,
        client_connections: WorkerClientConnections | None = None,
    ) -> None:
        self._subagent_capability = subagent_capability
        self._secrets = secrets
        self._web = web
        self._memory = memory
        self._prepared_memory: PreparedMemory | None = None
        self._configuration_capability = configuration_capability
        self._bound_secrets: BoundAgentSecrets | None = None
        self._environments = environments
        self._client_connections = client_connections
        self._external_tools = external_tools
        self._sessions = sessions
        self._run = run
        self._workspace_id = workspace_id
        self._catalog = catalog
        self._control = control
        self._committer = committer
        self._payloads = payloads
        self._sources = sources
        self._inputs = inputs
        self._model_resolver = model_resolver
        self._model_factory = model_factory
        self._skills = skills
        self._asset_publication = asset_publication
        self._async_results = async_results
        self._prepared_skills: dict[str | None, PreparedSkillRuntime] | None = None

    async def claim_state_writer(self) -> None:
        await self._control.claim_state_writer(self._run)

    async def validate_dependencies(self, context: AttemptContext) -> None:
        """Validate resources against the final claimed checkpoint."""
        config = self._control.current_state.envelope.effective_agent_config
        await context.authorization.initialize(
            self._sessions,
            principal=self._run.authority_principal,
            organization_id=self._run.organization_id,
            workspace_id=self._workspace_id,
            root_agent_id=self._run.agent_id,
            agent_ids=frozenset(edge.child_agent_id for edge, _ in inline_child_executions(config).values()),
            run_id=context.run_id,
            run_attempt_id=context.run_attempt_id,
            environment_id=self._run.environment_id,
            configuration_context=self._run.configuration_context,
        )
        if self._run.configuration_context is not None:
            if self._configuration_capability is None:
                raise RunError("Configuration tools are unavailable.", code="configuration_worker_incompatible")
            validate_configuration_definition(run=self._run, config=config)
            await to_thread.run_sync(KnowledgeFiles().validate)
        if self._memory is None:
            raise RunError("Memory runtime is unavailable.", code="memory_binding_unavailable")
        self._prepared_memory = await self._memory.prepare(
            run=self._run,
            workspace_id=self._workspace_id,
            config=config,
            current_context=lambda: self._control.current_context,
        )
        if self._web is not None:
            await self._web.validate(
                run=self._run,
                workspace_id=self._workspace_id,
                config=config,
                current_context=lambda: self._control.current_context,
            )
        elif graph_uses_web(config):
            raise RunError("Web runtime is unavailable.", code="web_provider_unavailable")
        bindings = self._control.current_state.envelope.secret_bindings
        if bindings or graph_secret_requirements(config):
            if self._secrets is None:
                raise RuntimeError("Agent Secret runtime is unavailable")
            self._bound_secrets = self._secrets.bind(
                run=self._run,
                workspace_id=self._workspace_id,
                config=config,
                bindings=bindings,
                current_attempt=lambda: self._control.current_context,
            )
            await self._bound_secrets.validate()
        prepared = self._control.current_state.envelope.prepared_plugins
        if prepared is None:
            prepared = await to_thread.run_sync(partial(prepare_agent_plugins, self._catalog, config))
        await to_thread.run_sync(
            partial(
                AgentReconstructor(self._catalog).validate,
                agent_id=self._run.agent_id,
                agent_revision_id=self._run.agent_revision_id,
                effective_config=config,
                subagent_capability=self._subagent_capability(),
                prepared_plugins=prepared,
            )
        )
        if self._control.current_state.envelope.prepared_plugins is None:
            await self._control.prepare_plugins(prepared)
        selection = await validate_run_environment(self._environments, self._control.current_context)
        self._prepared_skills = await validate_agent_resources(
            sessions=self._sessions,
            run=self._run,
            workspace_id=self._workspace_id,
            config=config,
            current_context=lambda: self._control.current_context,
            skills=self._skills,
            external_tools=self._external_tools,
            working_directory=selection.descriptor.working_directory if selection is not None else "/",
        )

    @asynccontextmanager
    async def open_runtime(self, context: AttemptContext) -> AsyncIterator[HarnessInvocation[Any]]:
        async with attempt_resource_stack(cleanup_timeout_seconds=context.cleanup_timeout.total_seconds()) as stack:
            stack.callback(self._sources.close)
            invocation = await self._prepare(context, stack)
            if self._run.configuration_context is not None:
                environment = await to_thread.run_sync(KnowledgeFiles().environment)
                invocation = replace(
                    invocation,
                    environment=MountedHarnessEnvironments(
                        entries={
                            "builtin-skills": EnvironmentMount(environment, access=EnvironmentAccess("read_only"))
                        },
                        default_environment="builtin-skills",
                    ),
                )
                yield invocation
                return
            environment = await prepare_run_environment(
                self._environments, context, client_connections=self._client_connections
            )
            if environment is not None:
                stack.push_async_callback(environment.close)
            mounted = MountedHarnessEnvironments(
                entries=(
                    {"workspace": EnvironmentMount(environment, access=EnvironmentAccess(environment.access))}
                    if environment is not None
                    else {}
                )
            )

            async def prepare_mount(mount: AcceptedRunMount):
                candidate = await prepare_run_environment(
                    self._environments,
                    self._control.current_context,
                    client_connections=self._client_connections,
                    mount=mount,
                )
                if candidate is None:
                    raise RuntimeError("An accepted additional mount has no Environment")
                return candidate

            await self._control.bind_environment_mounts(
                RunMountRuntime(
                    runtime=mounted.runtime,
                    observations=RunMountObservations(self._sessions, clock=self._environments.clock),
                    current_attempt=lambda: self._control.current_context,
                    prepare=prepare_mount,
                )
            )
            yield replace(invocation, environment=mounted)

    async def _prepare(self, context: AttemptContext, stack: AsyncExitStack) -> HarnessInvocation[Any]:
        run = self._run
        config = self._control.current_state.envelope.effective_agent_config
        if self._prepared_skills is None:
            raise RuntimeError("Attempt dependencies have not been validated")
        resources = await prepare_agent_resources(
            run=run,
            config=config,
            current_context=lambda: self._control.current_context,
            skills=self._prepared_skills,
            workspace_id=self._workspace_id,
            asset_publication=self._asset_publication,
            external_tools=self._external_tools,
            stack=stack,
        )

        def capabilities(context: AgentDefinitionReconstructionContext):
            selected = resources.for_definition(context)
            if context.is_root and run.configuration_context is not None:
                assert self._configuration_capability is not None
                selected = (
                    *selected,
                    self._configuration_capability(),
                    knowledge_capability(),
                    UserInteractionCapability(),
                )
            if not context.is_root:
                selected = (InlineRunControlCapability(self._control, agent_id=context.agent_id), *selected)
            if self._prepared_memory is None:
                raise RunError("Memory preparation is incomplete.", code="memory_binding_unavailable")
            memory = self._prepared_memory.for_node(context)
            if isinstance(memory, ActiveMemory):
                selected = (*selected, memory.capability)
            protocol_context = self._control.current_state.envelope.protocol_context
            if context.is_root and protocol_context is not None:
                selected = (*selected, ProtocolContextCapability(protocol_context))
            return (*selected, WorkerInputCapability(self._sources)) if context.is_root else selected

        def web_binding(node: AgentDefinitionReconstructionContext) -> WebBinding | None:
            selection = web_selection(node.config.toolsets)
            if selection is None:
                return None
            if self._web is None:
                raise RuntimeError("Web runtime is unavailable")
            return self._web.binding(
                run=run,
                workspace_id=self._workspace_id,
                agent_id=node.agent_id,
                selection=selection,
                current_context=lambda: self._control.current_context,
            )

        def run_bindings(node: AgentDefinitionReconstructionContext, bindings: RunBindings) -> RunBindings:
            return replace(bindings, web=web_binding(node))

        root_web = web_binding(
            AgentDefinitionReconstructionContext(
                agent_id=run.agent_id,
                agent_revision_id=run.agent_revision_id,
                content_digest=config.content_digest,
                is_root=True,
                config=config,
            )
        )
        prepared_plugins = self._control.current_state.envelope.prepared_plugins
        if prepared_plugins is None:
            raise RuntimeError("Plugin configuration has not been durably prepared")
        definition = AgentReconstructor(
            self._catalog, capability_provider=capabilities, run_bindings_provider=run_bindings
        ).reconstruct(
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            effective_config=config,
            prepared_plugins=prepared_plugins,
            subagent_capability=self._subagent_capability(),
        )
        resume = None
        accepted: AcceptedAgentInput | None = None
        if not self._control.current_state.envelope.initial_input_applied:
            payload = (
                run.input
                if run.input_object is None
                else (await self._payloads.read(run.organization_id, run.input_object)).payload
            )
            observe_input(payload)
            if run.input_kind is RunInputKind.agent_input:
                accepted = AcceptedAgentInput.model_validate(payload)
            elif run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}:
                feedback = (
                    WaitingRunContinueInput.model_validate(payload)
                    if run.input_kind is RunInputKind.waiting_continue
                    else WaitingRunFeedback.model_validate(payload)
                )
                if isinstance(feedback, WaitingRunContinueInput):
                    accepted = feedback.input
                deferred = self._control.current_state.envelope.host.deferred
                if deferred is None:
                    raise ValueError("Accepted feedback has no deferred continuation")
                results = DeferredToolResults()
                for resolution in feedback.resolutions:
                    if resolution.kind.value == "approval":
                        results.approvals[resolution.call_id] = (
                            True
                            if resolution.outcome.value == "approve"
                            else ToolDenied(resolution.reason or "Approval was denied.")
                        )
                    else:
                        results.calls[resolution.call_id] = (
                            ToolDenied("No response was supplied.")
                            if resolution.outcome.value == "no_response"
                            else resolution.result
                        )
                resume = DeferredToolResume(
                    TypeAdapter(DeferredToolRequests).validate_python(deferred.requests), results
                )
            elif run.input_kind is RunInputKind.async_subagent_result:
                async with short_session(self._sessions) as session:
                    row = await session.get(ThreadInboxRecord, run.trigger_entity_id)
                    if row is None or row.target_run_id != run.id or row.organization_id != run.organization_id:
                        raise ValueError("Accepted async result has no matching inbox authority")
                    entry = row.to_resource()
                if entry.payload != payload:
                    raise ValueError("Accepted async result input changed")
                accepted = AcceptedAgentInput.model_validate(
                    {
                        "schema_version": "1",
                        "content": [{"type": "text", "text": await self._async_results(entry)}],
                    }
                )
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="a13n.service", subject=run.authority_principal.principal_id),
            agent_instance_id=run.thread_id,
            actor=f"{run.authority_principal.principal_type}:{run.authority_principal.principal_id}",
            host_refs={"session_id": run.session_id, "thread_id": run.thread_id, "run_id": run.id},
        )
        input_source = ImmediateHarnessInput()
        if accepted is not None:

            async def input_factory(preparation: RunPreparationContext) -> RunInputValue:
                self._sources.environment = preparation.environment
                assert accepted is not None
                value = await self._inputs.map(accepted, run.id, config)
                return value

            input_source = MaterializedHarnessInput(input_factory)
        elif isinstance(self._control.current_state.envelope.outcome_candidate, CompletedOutcomeCandidate):

            async def continuation_factory(preparation: RunPreparationContext) -> RunInputValue:
                self._sources.environment = preparation.environment
                return await self._control.continuation_input(self._committer)

            input_source = MaterializedHarnessInput(continuation_factory)
        return HarnessInvocation(
            definition=definition,
            usage_limits=self._control.current_state.envelope.usage_limits,
            input=input_source,
            deferred_resume=resume,
            collaborators=HarnessCollaborators(
                instance=instance,
                web=root_web,
                capabilities=(self._bound_secrets.capability(),) if self._bound_secrets is not None else (),
                model_resolver=SnapshotRunModelResolver(
                    snapshots=resources.models,
                    organization_id=run.organization_id,
                    workspace_id=self._workspace_id,
                    provider_resolver=self._model_resolver,
                    model_factory=self._model_factory,
                ),
            ),
        )
