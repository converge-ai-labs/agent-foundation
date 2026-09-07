"""Reconstruct the accepted Agent and its input without retaining database sessions."""

from __future__ import annotations

from typing import Any

from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
    RunInputValue,
    RunPreparationContext,
)
from a13n_harness.capabilities import SkillsCapability
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from pydantic import TypeAdapter
from pydantic_ai import ToolDenied
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentRevision
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext, AgentReconstructor
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.runtime import SnapshotRunModelResolver
from a13n_service.skills.runtime import PreparedSkillRuntime, SkillRuntimePreparer
from a13n_service.storage import short_session
from a13n_service.subagents.result_delivery import AsyncSubagentResultMaterializer

from .attempts import AttemptContext
from .control_domain import ThreadInboxEntry, ThreadInboxKind, WaitingRunContinueInput, WaitingRunFeedback
from .control_models import ThreadInboxRecord
from .domain import Run, RunInputKind, RunPayloadObjectRef
from .harness_runtime import HarnessCollaborators, HarnessInvocation, ImmediateHarnessInput, MaterializedHarnessInput
from .input import AcceptedAgentInput, AgentInputMapper, native_input_adapter
from .objects import RunPayloadStore
from .run_control import RunAttemptControl
from .worker_input import WorkerInputCapability, WorkerInputSources


class WorkerAttemptPreparer:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        run: Run,
        workspace_id: str,
        catalog: HarnessPluginFactoryCatalog,
        control: RunAttemptControl,
        payloads: RunPayloadStore,
        sources: WorkerInputSources,
        model_resolver: LiveProviderResolver,
        model_factory: NativeModelFactory,
        skills: SkillRuntimePreparer,
        async_results: AsyncSubagentResultMaterializer,
    ) -> None:
        self._sessions = sessions
        self._run = run
        self._workspace_id = workspace_id
        self._catalog = catalog
        self._control = control
        self._payloads = payloads
        self._sources = sources
        self._model_resolver = model_resolver
        self._model_factory = model_factory
        self._skills = skills
        self._async_results = async_results
        self._children: dict[str, AgentRevision] = {}
        self._skill_runtime: PreparedSkillRuntime | None = None

    async def validate(self, context: AttemptContext) -> None:
        """Claim the final recovery state and validate dependencies before admission."""

        await self._control.claim_state(self._run)
        run = self._run
        config = self._control.current_state.envelope.effective_agent_config
        children: dict[str, AgentRevision] = {}
        pending = list(config.resolved_subagents)
        async with short_session(self._sessions) as session:
            await authorize_persisted_agent_principal_actions(
                session,
                principal=run.authority_principal,
                organization_id=run.organization_id,
                workspace_id=self._workspace_id,
                agent_id=run.agent_id,
                actions=frozenset({WorkspaceAction.agent_invoke}),
            )
            while pending:
                edge = pending.pop()
                if edge.child_agent_revision_id in children:
                    continue
                if len(children) >= 128:
                    raise ValueError("Accepted Agent graph exceeds the reconstruction bound")
                row = await session.scalar(
                    select(AgentRevisionRecord).where(
                        AgentRevisionRecord.id == edge.child_agent_revision_id,
                        AgentRevisionRecord.organization_id == run.organization_id,
                        AgentRevisionRecord.workspace_id == self._workspace_id,
                        AgentRevisionRecord.agent_id == edge.child_agent_id,
                    )
                )
                if row is None:
                    raise ValueError("Accepted child Agent revision is unavailable")
                await authorize_persisted_agent_principal_actions(
                    session,
                    principal=run.authority_principal,
                    organization_id=run.organization_id,
                    workspace_id=self._workspace_id,
                    agent_id=edge.child_agent_id,
                    actions=frozenset({WorkspaceAction.agent_invoke}),
                )
                child = row.to_resource()
                children[child.id] = child
                pending.extend(child.resolved_subagents)
        AgentReconstructor(self._catalog).validate(
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            effective_config=config,
            child_revisions=children,
        )
        skill_runtime = await self._skills.prepare(
            organization_id=run.organization_id,
            workspace_id=self._workspace_id,
            locks=config.skills,
        )

        self._children = children
        self._skill_runtime = skill_runtime

    async def prepare(self, context: AttemptContext) -> HarnessInvocation[Any]:
        """Construct live invocation inputs only after successful recovery admission."""

        run = self._run
        config = self._control.current_state.envelope.effective_agent_config
        children = self._children
        skill_runtime = self._skill_runtime
        if skill_runtime is None:
            raise RuntimeError("Attempt dependencies have not been validated")

        def capabilities(context: AgentDefinitionReconstructionContext):
            if context.is_root and skill_runtime.manager is not None:
                return (WorkerInputCapability(self._sources), SkillsCapability(skill_runtime.manager))
            return (WorkerInputCapability(self._sources),) if context.is_root else ()

        definition = AgentReconstructor(self._catalog, capability_provider=capabilities).reconstruct(
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            effective_config=config,
            child_revisions=children,
        )
        payload = (
            run.input
            if run.input_object is None
            else (await self._payloads.read(run.organization_id, run.input_object)).payload
        )
        resume = None
        accepted: AcceptedAgentInput | None = None
        if self._control.current_state.envelope.input_disposition == "pending":
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
                        results.approvals[resolution.call_id] = resolution.outcome.value == "approve"
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
            identity=AgentIdentityRef(issuer="foundation", subject=run.authority_principal.principal_id),
            agent_instance_id=run.thread_id,
            actor=f"{run.authority_principal.principal_type}:{run.authority_principal.principal_id}",
            host_refs={"session_id": run.session_id, "thread_id": run.thread_id, "run_id": run.id},
        )
        input_source = ImmediateHarnessInput()
        if accepted is not None:

            async def input_factory(preparation: RunPreparationContext) -> RunInputValue:
                self._sources.environment = preparation.environment
                assert accepted is not None
                value = await self._map(accepted, run.id)
                if value is None:
                    raise ValueError("Accepted Agent input produced no semantic content")
                return value

            input_source = MaterializedHarnessInput(input_factory)
        return HarnessInvocation(
            definition=definition,
            input=input_source,
            deferred_resume=resume,
            collaborators=HarnessCollaborators(
                instance=instance,
                model_resolver=SnapshotRunModelResolver(
                    snapshot=config.resolved_model.execution,
                    organization_id=run.organization_id,
                    workspace_id=self._workspace_id,
                    provider_resolver=self._model_resolver,
                    model_factory=self._model_factory,
                ),
                capabilities=(
                    () if skill_runtime.selection_capability is None else (skill_runtime.selection_capability,)
                ),
            ),
        )

    async def materialize_inbox(self, entry: ThreadInboxEntry) -> RunInputValue:
        if entry.kind is ThreadInboxKind.async_subagent_result:
            return await self._async_results(entry)
        payload = entry.payload
        if entry.payload_object is not None:
            stored = await self._payloads.read(
                entry.organization_id,
                RunPayloadObjectRef.model_validate(entry.payload_object.model_dump()),
            )
            if stored.run_id != entry.accepted_against_run_id or stored.payload_kind != "input":
                raise ValueError("Inbox payload object does not belong to its accepted Run")
            payload = stored.payload
        value = await self._map(AcceptedAgentInput.model_validate(payload), entry.id)
        if value is None:
            raise ValueError("Inbox entry produced no semantic content")
        return value

    async def _map(self, accepted: AcceptedAgentInput, instance_id: str) -> RunInputValue | None:
        mapper = AgentInputMapper(
            self._sources,
            {"native": native_input_adapter},
            max_binary_bytes=self._control.current_state.envelope.effective_agent_config.protocol.limits.max_input_bytes,
        )
        return await mapper.map(
            accepted,
            input_instance_id=instance_id,
            adapter=self._control.current_state.envelope.effective_agent_config.input_adapter,
            environment=self._sources if self._sources.environment is not None else None,
        )
