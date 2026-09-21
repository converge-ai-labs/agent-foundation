"""Prepare selected capabilities and assemble the accepted root and inline Agents."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    RunBindings,
)
from a13n_harness.capabilities import SubagentCapability, WebBinding
from a13n_harness.errors import RunError
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.execution_graph import inline_child_executions
from a13n_service.agents.plugin_preparation import prepare_agent_plugins
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext, AgentReconstructor
from a13n_service.agents.toolsets import web_selection
from a13n_service.assets.runtime import AssetRuntime
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.runtime import validate_run_environment
from a13n_service.models.media_runtime import FileMediaUnderstanding, FileMediaUnderstandingCapability
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.runtime import SnapshotRunModelResolver
from a13n_service.secrets.agent_inputs import graph_secret_requirements
from a13n_service.secrets.agent_runtime import AgentSecretRuntime, BoundAgentSecrets
from a13n_service.skills.runtime import PreparedSkillRuntime, SkillRuntimePreparer
from a13n_service.web.runtime import WebRuntime, graph_uses_web

from .agent_resources import prepare_agent_resources, validate_agent_resources
from .attempts import AttemptContext
from .domain import Run
from .execution_source import ExecutionSource
from .harness_control import InlineRunControlCapability
from .harness_runtime import (
    HarnessCollaborators,
    HarnessInvocation,
    ImmediateHarnessInput,
)
from .ports.memory import ActiveMemory, ExecutionMemoryRuntime, PreparedMemory
from .protocol_context import ProtocolContextCapability
from .run_control import RunAttemptControl
from .worker_input import WorkerInputCapability, WorkerInputSources


@dataclass(frozen=True, slots=True)
class PreparedRuntime:
    """Validated dependencies consumed by runtime assembly after preparation commits."""

    config: EffectiveAgentConfig
    memory: PreparedMemory
    secrets: BoundAgentSecrets | None
    skills: dict[str | None, PreparedSkillRuntime]


class AgentRuntimeAssembler:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        run: Run,
        workspace_id: str,
        catalog: HarnessPluginFactoryCatalog,
        control: RunAttemptControl,
        sources: WorkerInputSources,
        source: ExecutionSource,
        model_resolver: LiveProviderResolver,
        model_factory: NativeModelFactory,
        skills: SkillRuntimePreparer,
        asset_publication: AssetRuntime,
        environments: EnvironmentLifecycle,
        external_tools: ExternalToolRuntime,
        subagent_capability: Callable[[], SubagentCapability],
        secrets: AgentSecretRuntime | None = None,
        web: WebRuntime | None = None,
        memory: ExecutionMemoryRuntime | None = None,
    ) -> None:
        self.source = source
        self._subagent_capability = subagent_capability
        self._secrets = secrets
        self._web = web
        self._memory = memory
        self._environments = environments
        self._external_tools = external_tools
        self._sessions = sessions
        self._run = run
        self._workspace_id = workspace_id
        self._catalog = catalog
        self._control = control
        self._sources = sources
        self._model_resolver = model_resolver
        self._model_factory = model_factory
        self._skills = skills
        self._asset_publication = asset_publication

    async def prepare(self, context: AttemptContext) -> PreparedRuntime:
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
            root_policy=self.source.root_policy,
        )
        await self.source.validate(config)
        if self._memory is None:
            raise RunError("Memory runtime is unavailable.", code="memory_binding_unavailable")
        memory = await self._memory.prepare(
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
        secrets: BoundAgentSecrets | None = None
        bindings = self._control.current_state.envelope.secret_bindings
        if bindings or graph_secret_requirements(config):
            if self._secrets is None:
                raise RuntimeError("Agent Secret runtime is unavailable")
            secrets = self._secrets.bind(
                run=self._run,
                workspace_id=self._workspace_id,
                config=config,
                bindings=bindings,
                current_attempt=lambda: self._control.current_context,
            )
            await secrets.validate()
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
        skills = await validate_agent_resources(
            sessions=self._sessions,
            run=self._run,
            workspace_id=self._workspace_id,
            config=config,
            current_context=lambda: self._control.current_context,
            skills=self._skills,
            external_tools=self._external_tools,
            source=self.source,
            working_directory=selection.descriptor.working_directory if selection is not None else "/",
        )

        return PreparedRuntime(config, memory, secrets, skills)

    async def assemble(self, prepared: PreparedRuntime, stack: AsyncExitStack) -> HarnessInvocation[Any]:
        run = self._run
        config = prepared.config
        resources = await prepare_agent_resources(
            run=run,
            config=config,
            current_context=lambda: self._control.current_context,
            skills=prepared.skills,
            workspace_id=self._workspace_id,
            asset_publication=self._asset_publication,
            external_tools=self._external_tools,
            source=self.source,
            stack=stack,
        )

        model_resolver = SnapshotRunModelResolver(
            snapshots=resources.models,
            organization_id=run.organization_id,
            workspace_id=self._workspace_id,
            provider_resolver=self._model_resolver,
            model_factory=self._model_factory,
        )
        root_media = (
            FileMediaUnderstanding(config.media_understanding, model_resolver) if config.media_understanding else None
        )

        def capabilities(context: AgentDefinitionReconstructionContext):
            selected = resources.for_definition(context)
            if context.config.media_understanding:
                selected = (*selected, FileMediaUnderstandingCapability())
            if not context.is_root:
                selected = (InlineRunControlCapability(self._control, agent_id=context.agent_id), *selected)
            memory = prepared.memory.for_node(context)
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
            media = (
                FileMediaUnderstanding(node.config.media_understanding, model_resolver)
                if node.config.media_understanding
                else None
            )
            return replace(
                bindings,
                web=web_binding(node),
                file_media_understanding=media,
            )

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
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="a13n.service", subject=run.authority_principal.principal_id),
            agent_instance_id=run.thread_id,
            actor=f"{run.authority_principal.principal_type}:{run.authority_principal.principal_id}",
            host_refs={"session_id": run.session_id, "thread_id": run.thread_id, "run_id": run.id},
        )
        return HarnessInvocation(
            definition=definition,
            usage_limits=self._control.current_state.envelope.usage_limits,
            input=ImmediateHarnessInput(),
            collaborators=HarnessCollaborators(
                instance=instance,
                web=root_web,
                capabilities=(prepared.secrets.capability(),) if prepared.secrets is not None else (),
                file_media_understanding=root_media,
                model_resolver=model_resolver,
            ),
        )
