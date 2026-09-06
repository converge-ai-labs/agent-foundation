"""Attempt-owned acquisition of accepted inputs outside database sessions."""

from __future__ import annotations

import posixpath
from collections.abc import AsyncIterable, Callable
from contextlib import AsyncExitStack

import httpx2
from a13n_harness import AgentContext, DeferredToolResume, RunInputValue, RunPreparationContext
from a13n_harness.environment.providers import BoundEnvironment
from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import InputAdapterConfig
from a13n_service.assets.service import AssetService
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam import AuthenticatedActor
from a13n_service.public_errors import PublicError
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now

from .attempts import AttemptContext, read_attempt_lease
from .control_domain import ThreadInboxEntry, ThreadInboxKind, WaitingRunContinueInput, WaitingRunFeedback
from .domain import Run, RunInputKind
from .feedback import map_waiting_feedback
from .harness_runtime import HarnessInput, ImmediateHarnessInput, MaterializedHarnessInput
from .inbox import InboxInputNotReady
from .input import (
    AcceptedAgentInput,
    AcceptedBinaryContent,
    AcquiredBinary,
    AgentInputError,
    AgentInputMapper,
    AssetBinarySource,
    BinaryContentSource,
    PathBinarySource,
    native_input_adapter,
)
from .models import RunRecord
from .objects import RunPayloadStore, RunStateStore


class AttemptInputRuntime(AbstractCapability[AgentContext]):
    """One input reader, Environment binding, and steering adapter per Attempt."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        payloads: RunPayloadStore,
        states: RunStateStore,
        assets: AssetService,
        http: httpx2.AsyncClient,
        stack: AsyncExitStack,
        authority: Callable[[], AttemptContext],
        *,
        max_binary_bytes: int,
    ) -> None:
        self._sessions = sessions
        self._payloads = payloads
        self._states = states
        self._assets = assets
        self._http = http
        self._stack = stack
        self._authority = authority
        self._environment: BoundEnvironment | None = None
        self._actor: AuthenticatedActor | None = None
        self._adapter: InputAdapterConfig | None = None
        self._mapper = AgentInputMapper(self, {"native": native_input_adapter}, max_binary_bytes=max_binary_bytes)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        # Also bind on recovery, when the initial input factory is deliberately skipped.
        self._environment = ctx.deps.environment
        return self

    async def require_current(self) -> None:
        async with short_session(self._sessions) as database:
            await read_attempt_lease(database, self._authority(), utc_now())

    async def prepare(
        self, run: Run, actor: AuthenticatedActor, adapter: InputAdapterConfig, *, input_pending: bool
    ) -> tuple[HarnessInput, DeferredToolResume | None]:
        self._actor, self._adapter = actor, adapter
        if adapter.adapter_key != "native" or adapter.config:
            raise AgentInputError("input_adapter_unavailable", "The accepted input adapter is unavailable.")
        if not input_pending:
            return ImmediateHarnessInput(), None
        value = run.input
        if run.input_object is not None:
            value = (await self._payloads.verify_reference(run.tenant_id, run.id, "input", run.input_object)).payload
        deferred = None
        accepted: AcceptedAgentInput | None
        if run.input_kind is RunInputKind.agent_input:
            accepted = AcceptedAgentInput.model_validate(value)
        elif run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}:
            if run.input_kind is RunInputKind.waiting_continue:
                continued = WaitingRunContinueInput.model_validate(value)
                feedback = WaitingRunFeedback.model_validate(continued.model_dump(exclude={"input"}))
                accepted = continued.input
            else:
                feedback = WaitingRunFeedback.model_validate(value)
                accepted = None
            deferred = await self._feedback(run, feedback)
        else:
            raise AgentInputError("run_input_unsupported", "This Worker cannot execute the accepted input kind.")
        if accepted is None:
            return ImmediateHarnessInput(), deferred
        if not any(isinstance(block, AcceptedBinaryContent) for block in accepted.content):
            return ImmediateHarnessInput(await self._map(accepted, run.id)), deferred

        async def materialize(context: RunPreparationContext) -> RunInputValue:
            self._environment = context.environment
            result = await self._map(accepted, run.id)
            if result is None:
                raise AgentInputError("input_empty", "Materialized input cannot be empty.")
            return result

        return MaterializedHarnessInput(materialize), deferred

    async def materialize_inbox(self, entry: ThreadInboxEntry) -> RunInputValue:
        if entry.kind is not ThreadInboxKind.steer or entry.payload_object is not None:
            raise AgentInputError("inbox_input_unsupported", "The accepted inbox payload cannot be materialized.")
        try:
            accepted = AcceptedAgentInput.model_validate(entry.payload)
        except ValidationError as error:
            raise AgentInputError("inbox_input_invalid", "The retained inbox input is invalid.") from error
        if self._environment is None and any(isinstance(block, AcceptedBinaryContent) for block in accepted.content):
            raise InboxInputNotReady()
        result = await self._map(accepted, entry.id)
        if result is None:
            raise AgentInputError("input_empty", "Steering input cannot be empty.")
        return result

    async def _map(self, accepted: AcceptedAgentInput, input_id: str) -> RunInputValue | None:
        await self.require_current()
        if self._adapter is None:
            raise RuntimeError("Attempt input has not been prepared")
        try:
            result = await self._mapper.map(
                accepted,
                input_instance_id=input_id,
                adapter=self._adapter,
                environment=self if self._environment is not None else None,
            )
        except (PublicError, EndpointPolicyError, httpx2.TransportError) as error:
            raise AgentInputError(
                "input_source_unavailable", "The accepted input source could not be acquired."
            ) from error
        await self.require_current()
        return result

    async def open(self, source: BinaryContentSource, *, max_bytes: int) -> AcquiredBinary:
        await self.require_current()
        if isinstance(source, PathBinarySource):
            if self._environment is None or source.environment_binding != "workspace":
                raise AgentInputError("input_environment_unavailable", "The input Environment is unavailable.")
            return AcquiredBinary(self._environment.files.read_bytes_stream(source.path), None)
        if isinstance(source, AssetBinarySource):
            if self._actor is None:
                raise RuntimeError("Attempt input Principal has not been prepared")
            asset = await self._assets.require_for_use(actor=self._actor, asset_id=source.asset_id)
            if asset.size_bytes > max_bytes:
                raise AgentInputError("input_too_large", "Binary input exceeds its size limit.")
            prepared = await self._assets.prepare_content_for_use(actor=self._actor, asset_id=source.asset_id)
            self._stack.push_async_callback(prepared.content.remove)
            return AcquiredBinary(prepared.content.chunks(), prepared.asset.media_type)
        # Input URLs never inherit the Model Provider's private-network allowlist.
        await EndpointPolicy().validate(source.url, resolve_dns=True)
        response = await self._stack.enter_async_context(self._http.stream("GET", source.url))
        if response.status_code != 200:
            raise AgentInputError("input_source_unavailable", "The accepted input URL could not be read.")
        length = response.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > max_bytes):
            raise AgentInputError("input_too_large", "Binary input exceeds its size limit.")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower() or None
        return AcquiredBinary(response.aiter_bytes(), media_type)

    async def replace(self, path: str, chunks: AsyncIterable[bytes]) -> None:
        await self.require_current()
        if self._environment is None:
            raise AgentInputError("input_environment_unavailable", "The input Environment is unavailable.")
        await self._environment.files.mkdir(posixpath.dirname(path), parents=True, exist_ok=True)
        await self._environment.files.write_bytes_stream(path, chunks, mode="upsert")
        await self.require_current()

    async def _feedback(self, run: Run, feedback: WaitingRunFeedback) -> DeferredToolResume:
        if run.parent_run_id != feedback.waiting_run_id:
            raise AgentInputError("waiting_feedback_invalid", "Feedback does not match its accepted parent.")
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == run.tenant_id,
                    RunRecord.id == feedback.waiting_run_id,
                    RunRecord.thread_id == run.thread_id,
                )
            )
            parent = record.to_resource() if record is not None else None
        if parent is None or parent.sealed_state is None:
            raise AgentInputError("waiting_feedback_invalid", "The sealed feedback parent is unavailable.")
        state = await self._states.read(run.tenant_id, parent.id)
        sealed = parent.sealed_state
        if (
            state.digest_sha256 != sealed.digest_sha256
            or len(state.body) != sealed.size_bytes
            or state.info.content_type != sealed.content_type
            or state.envelope.checkpoint_seq != sealed.checkpoint_seq
            or state.digest_sha256 != feedback.sealed_state_digest_sha256
        ):
            raise AgentInputError("waiting_feedback_invalid", "The sealed feedback parent failed verification.")
        return map_waiting_feedback(feedback, state.envelope)
