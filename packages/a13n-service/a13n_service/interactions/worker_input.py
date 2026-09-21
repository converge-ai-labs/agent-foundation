"""Acquire accepted input through fresh, authorized Worker resources."""

from __future__ import annotations

import posixpath
from collections.abc import AsyncIterable, AsyncIterator

import httpx2
from a13n_harness import AgentContext, DeferredToolResume, RunInputValue, RunPreparationContext
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import TypeAdapter
from pydantic_ai import RunContext, ToolDenied
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.assets.errors import AssetError
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.queries import require_active_asset
from a13n_service.iam.attempts import AttemptAuthorization
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_workspace_principal_action
from a13n_service.observability import observe_input
from a13n_service.storage import short_session
from a13n_service.subagents.result_delivery import AsyncSubagentResultMaterializer

from .control_domain import ThreadInboxEntry, ThreadInboxKind, WaitingRunContinueInput, WaitingRunFeedback
from .control_models import ThreadInboxRecord
from .domain import Run, RunInputKind, RunPayloadObjectRef
from .harness_control import RunControlCapability
from .harness_results import AttemptCommitter
from .harness_runtime import HarnessInput, ImmediateHarnessInput, MaterializedHarnessInput
from .input import (
    AcceptedAgentInput,
    AcquiredBinary,
    AgentInputError,
    AgentInputMapper,
    BinaryContentSource,
    PathBinarySource,
    UrlBinarySource,
    native_input_adapter,
)
from .objects import RunPayloadStore
from .run_control import RunAttemptControl
from .state import CompletedOutcomeCandidate


class WorkerInputSources:
    """Keep Environment access inside the logical Harness lifetime and clear it at cleanup."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        assets: AssetObjectStore,
        http: httpx2.AsyncClient,
        endpoint_policy: EndpointPolicy,
        run: Run,
        workspace_id: str,
        authorization: AttemptAuthorization,
    ) -> None:
        self._sessions = sessions
        self._assets = assets
        self._http = http
        self._endpoint_policy = endpoint_policy
        self._run = run
        self._workspace_id = workspace_id
        self._authorization = authorization
        self.environment: BoundEnvironment | None = None

    async def open(self, source: BinaryContentSource, *, max_bytes: int) -> AcquiredBinary:
        if isinstance(source, UrlBinarySource):
            url = await self._endpoint_policy.validate(source.url, resolve_dns=True)

            # Open inside the iterator so cancellation always closes the HTTP response.
            async def chunks() -> AsyncIterator[bytes]:
                async with self._http.stream("GET", url, follow_redirects=False) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        yield chunk

            return AcquiredBinary(chunks(), None)
        if isinstance(source, PathBinarySource):
            environment = self._require_environment()
            selection = await environment.resolve_files(source.path, alias=source.environment_binding)

            async def path_chunks() -> AsyncIterator[bytes]:
                async with environment.open_files(selection) as files:
                    async for chunk in files.read_bytes_stream(selection.logical_path):
                        yield chunk

            return AcquiredBinary(path_chunks(), None)
        async with short_session(self._sessions) as session:
            await authorize_persisted_workspace_principal_action(
                session,
                principal=self._run.authority_principal,
                organization_id=self._run.organization_id,
                workspace_id=self._workspace_id,
                action=WorkspaceAction.asset_use,
                snapshot=self._authorization.snapshot,
            )
            try:
                asset = await require_active_asset(
                    session,
                    organization_id=self._run.organization_id,
                    workspace_id=self._workspace_id,
                    asset_id=source.asset_id,
                )
            except AssetError as error:
                raise AgentInputError("input_asset_unavailable", "The accepted Asset is unavailable") from error
        if asset.size_bytes > max_bytes:
            raise AgentInputError("input_binary_too_large", "The Asset exceeds the accepted input limit")

        async def asset_chunks() -> AsyncIterator[bytes]:
            # The mapper can reject media metadata without iterating. Stage lazily
            # so that such a rejection cannot strand a temporary Asset file.
            staged = await self._assets.prepare_verified_content(asset)
            try:
                async for chunk in staged.chunks():
                    yield chunk
            finally:
                await staged.remove()

        return AcquiredBinary(asset_chunks(), asset.media_type)

    async def replace(self, path: str, chunks: AsyncIterable[bytes]) -> str:
        environment = self._require_environment()
        selection = await environment.resolve_files(path, alias="workspace")
        if selection.mount_path is None:
            raise AgentInputError("input_environment_unavailable", "The input Environment has no aggregate path")
        path = selection.mount_path.rstrip("/") + selection.resolved_path.path
        files = environment.files
        await files.mkdir(posixpath.dirname(path), parents=True, exist_ok=True)
        await files.write_bytes_stream(path, chunks, mode="upsert")
        return path

    def close(self) -> None:
        self.environment = None

    def _require_environment(self) -> BoundEnvironment:
        if self.environment is None:
            raise AgentInputError("input_environment_unavailable", "The Run Environment is not entered")
        return self.environment


class WorkerInputCapability(AbstractCapability[AgentContext]):
    """Bind only the current logical Run's entered input Environment."""

    id = "a13n.service.input-environment"

    def __init__(self, sources: WorkerInputSources) -> None:
        self._sources = sources

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(RunControlCapability,))

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        self._sources.environment = ctx.deps.environment
        return self


class WorkerInputMaterializer:
    """Map initial and inbox inputs through the same fresh Worker resources."""

    def __init__(
        self,
        sources: WorkerInputSources,
        payloads: RunPayloadStore,
        async_results: AsyncSubagentResultMaterializer,
    ) -> None:
        self._sources = sources
        self._payloads = payloads
        self._async_results = async_results

    async def inbox(self, entry: ThreadInboxEntry, config: EffectiveAgentConfig) -> RunInputValue:
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
        return await self.map(AcceptedAgentInput.model_validate(payload), entry.id, config)

    async def map(self, accepted: AcceptedAgentInput, instance_id: str, config: EffectiveAgentConfig) -> RunInputValue:
        mapper = AgentInputMapper(
            self._sources,
            {"native": native_input_adapter},
            max_binary_bytes=config.protocol.limits.max_input_bytes,
        )
        value = await mapper.map(
            accepted,
            input_instance_id=instance_id,
            adapter=config.input_adapter,
            environment=self._sources if self._sources.environment is not None else None,
        )
        if value is None:
            raise ValueError("Accepted input produced no semantic content")
        return value

    async def restore(
        self,
        run: Run,
        control: RunAttemptControl,
        committer: AttemptCommitter,
        sessions: async_sessionmaker[AsyncSession],
    ) -> tuple[HarnessInput, DeferredToolResume | None]:
        """Restore accepted/resumed input; acquire binary content only after Environment entry."""
        config = control.current_state.envelope.effective_agent_config
        resume = None
        accepted: AcceptedAgentInput | None = None
        if not control.current_state.envelope.initial_input_applied:
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
                deferred = control.current_state.envelope.host.deferred
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
                async with short_session(sessions) as session:
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
        input_source = ImmediateHarnessInput()
        if accepted is not None:

            async def input_factory(preparation: RunPreparationContext) -> RunInputValue:
                self._sources.environment = preparation.environment
                assert accepted is not None
                value = await self.map(accepted, run.id, config)
                return value

            input_source = MaterializedHarnessInput(input_factory)
        elif isinstance(control.current_state.envelope.outcome_candidate, CompletedOutcomeCandidate):

            async def continuation_factory(preparation: RunPreparationContext) -> RunInputValue:
                self._sources.environment = preparation.environment
                return await control.continuation_input(committer)

            input_source = MaterializedHarnessInput(continuation_factory)
        return input_source, resume
