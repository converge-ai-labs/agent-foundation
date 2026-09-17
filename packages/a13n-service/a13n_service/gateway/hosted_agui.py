"""Hosted AG-UI input binding and lifecycle projection."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from time import monotonic
from typing import Any, Literal

import anyio
from ag_ui.core import (
    AssistantMessage,
    BinaryInputContent,
    Event,
    Message,
    RunAgentInput,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentConfig, AgentRunOverride, ClientToolDefinition
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.digests import digest_request
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.ids import new_object_id
from a13n_service.interactions.acceptance import RunAcceptanceReceipt
from a13n_service.interactions.command_values import (
    ContinueRunCommand,
    ForkRunCommand,
    StartRunCommand,
    WaitingContinueRunCommand,
)
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.control_domain import (
    CompletePendingResolution,
    InterruptRequest,
    SubmittedPendingResolution,
    WaitingRunFeedbackRequest,
)
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.input import AgentInput
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.protocol_context import ProtocolInputContext
from a13n_service.lifecycle import LifecycleEvent
from a13n_service.run_stream import (
    CompleteRunStream,
    RedisRunStream,
    RunStreamEntry,
    RunStreamError,
    RunStreamReplayGap,
)
from a13n_service.run_stream.domain import RecoveryPayload
from a13n_service.storage import ObjectNotFound, short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .agui_replay import (
    HostedAguiDeliveryEvent,
    HostedAguiReplayError,
    HostedAguiReplaySnapshot,
    HostedAguiReplayStore,
    HostedAguiReplayUnavailable,
)
from .models import AguiRunBindingRecord, AguiThreadBindingRecord

logger = logging.getLogger("a13n_service.gateway.hosted_agui")
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_JSON_VALUE = TypeAdapter(JsonValue)
_EVENT = TypeAdapter(Event)
_MESSAGE = TypeAdapter(Message)
_RESOLUTIONS = TypeAdapter(tuple[SubmittedPendingResolution, ...])
_VISIBLE_EVENTS = frozenset(
    {
        "text_message_start",
        "text_message_content",
        "text_message_end",
        "tool_call_start",
        "tool_call_args",
        "tool_call_end",
        "tool_call_result",
    }
)


class HostedAguiError(ApplicationError):
    """A bounded Hosted AG-UI adapter failure."""


class HostedAguiCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    thread_id: str = Field(alias="threadId")
    run_id: str = Field(alias="runId")


class HostedAguiCancelReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    thread_id: str = Field(alias="threadId")
    run_id: str = Field(alias="runId")
    status: Literal["cancelled"] = "cancelled"


@dataclass(frozen=True, slots=True)
class HostedAguiBinding:
    organization_id: str
    workspace_id: str
    thread_binding_id: str
    run_binding_id: str
    agent_id: str
    agent_revision_id: str
    external_thread_id: str
    external_run_id: str
    run_id: str
    request_digest_sha256: str


@dataclass(frozen=True, slots=True)
class HostedAguiAttachment:
    actor: AuthenticatedActor
    binding: HostedAguiBinding
    after_ordinal: int


@dataclass(frozen=True, slots=True)
class _MappedAguiInput:
    input: AgentInput | None
    resolutions: tuple[SubmittedPendingResolution, ...] | None
    agent_revision_id: str
    config_override: AgentRunOverride | None
    protocol_context: ProtocolInputContext


class _A13nForwardedProps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
    resume: tuple[dict[str, JsonValue], ...] = Field(max_length=256)


class _HostedForwardedProps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a13n: _A13nForwardedProps


class HostedAguiTerminalProjector:
    """Persist Hosted AG-UI terminal delivery from one complete Native source."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        replay: HostedAguiReplayStore,
    ) -> None:
        self._sessions = sessions
        self._replay = replay

    async def project(self, event: LifecycleEvent, source: CompleteRunStream) -> None:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(AguiRunBindingRecord, AguiThreadBindingRecord, RunRecord)
                    .join(
                        AguiThreadBindingRecord,
                        AguiThreadBindingRecord.id == AguiRunBindingRecord.thread_binding_id,
                    )
                    .join(
                        RunRecord,
                        and_(
                            RunRecord.organization_id == AguiRunBindingRecord.organization_id,
                            RunRecord.id == AguiRunBindingRecord.run_id,
                        ),
                    )
                    .where(
                        AguiRunBindingRecord.organization_id == event.organization_id,
                        AguiRunBindingRecord.run_id == event.run_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return
        run_binding, thread_binding, run = row
        binding = _binding(run_binding, thread_binding)
        try:
            snapshot = _build_replay_snapshot(binding, run, source.entries)
            await self._replay.publish(binding.organization_id, snapshot)
        except HostedAguiReplayUnavailable:
            logger.info(
                "Hosted AG-UI Run closed without retained replay",
                extra={"run_id": event.run_id, "lifecycle_event_id": event.id},
            )


class HostedAguiService:
    """Map standard AG-UI calls to canonical Service interaction commands."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        commands: InteractionCommands,
        stream: RedisRunStream,
        hosted_replay: HostedAguiReplayStore,
        *,
        page_size: int,
        poll_interval_seconds: float,
        heartbeat_interval_seconds: float,
        authorization_interval_seconds: float,
        maximum_lifetime_seconds: float,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._commands = commands
        self._stream = stream
        self._hosted_replay = hosted_replay
        self._page_size = page_size
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._authorization_interval_seconds = authorization_interval_seconds
        self._maximum_lifetime_seconds = maximum_lifetime_seconds
        self._clock = clock

    async def accept(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        request: RunAgentInput,
        last_event_id: str | None,
    ) -> HostedAguiAttachment:
        _validate_external_id(request.thread_id, name="threadId")
        _validate_external_id(request.run_id, name="runId")
        if request.parent_run_id is not None:
            _validate_external_id(request.parent_run_id, name="parentRunId")
        request_json = _JSON_OBJECT.validate_python(
            request.model_dump(mode="json", by_alias=True, exclude_none=True),
            strict=True,
        )
        request_digest = digest_request(request_json)
        existing = await self._load_run_binding(
            actor=actor,
            agent_id=agent_id,
            external_thread_id=request.thread_id,
            external_run_id=request.run_id,
        )
        if existing is not None:
            if existing.request_digest_sha256 != request_digest:
                raise HostedAguiError(
                    "agui_run_id_conflict",
                    "The AG-UI runId was already used with different input.",
                    category=ErrorCategory.conflict,
                )
            await self._authorize_read(actor=actor, binding=existing)
            return await self._attachment(actor=actor, binding=existing, cursor=last_event_id)

        thread = await self._load_thread_binding(
            actor=actor,
            agent_id=agent_id,
            external_thread_id=request.thread_id,
        )
        latest: AguiRunBindingRecord | None = None
        selected_parent: AguiRunBindingRecord | None = None
        source: RunRecord | None = None
        service_thread: ThreadRecord | None = None
        historical_parent = False
        if thread is not None:
            latest = await self._load_latest_run(thread.id)
            selected_parent = (
                latest
                if request.parent_run_id is None
                else await self._load_thread_run_binding(thread.id, request.parent_run_id)
            )
            if latest is None or selected_parent is None:
                raise HostedAguiError(
                    "agui_parent_not_found",
                    "The selected AG-UI parent Run is unavailable.",
                    category=ErrorCategory.conflict,
                )
            historical_parent = selected_parent.id != latest.id
            source, service_thread = await self._load_continuation_source(
                selected_parent.run_id,
                None if historical_parent else thread.active_thread_id,
            )
        reuse_waiting_surface = (
            source is not None and not historical_parent and source.status == RunStatus.waiting.value
        )
        if reuse_waiting_surface:
            assert selected_parent is not None and thread is not None
            waiting_binding = _binding(selected_parent, thread)
            await self._authorize_action(
                actor=actor,
                binding=waiting_binding,
                action=WorkspaceAction.run_feedback,
            )
            if not _has_feedback_input(request):
                await self._authorize_action(
                    actor=actor,
                    binding=waiting_binding,
                    action=WorkspaceAction.run_continue,
                )
        revision_id, config = await self._load_protocol_revision(
            actor=actor,
            agent_id=agent_id,
            revision_id=selected_parent.agent_revision_id if reuse_waiting_surface and selected_parent else None,
            authorize_invoke=not reuse_waiting_surface,
            trusted_organization_id=(
                selected_parent.organization_id if reuse_waiting_surface and selected_parent else None
            ),
        )
        mapped = await self._validate_and_map_input(
            request,
            request_json=request_json,
            thread=thread,
            history=selected_parent,
            revision_id=revision_id,
            config=config,
            reuse_waiting_surface=reuse_waiting_surface,
        )
        thread_binding_id = new_object_id("aguitb") if thread is None else thread.id
        run_binding_id = new_object_id("aguirb")
        now = assume_utc(self._clock())

        binding = _PreparedAguiBinding(
            actor=actor,
            agent_id=agent_id,
            request=request,
            request_json=request_json,
            request_digest=request_digest,
            thread=thread,
            thread_binding_id=thread_binding_id,
            run_binding_id=run_binding_id,
            selected_parent=selected_parent,
            now=now,
        )
        bind = partial(_persist_agui_binding, prepared=binding)

        idempotency_key = f"agui-{hashlib.sha256(request.run_id.encode()).hexdigest()}"
        if thread is None:
            if request.parent_run_id is not None:
                raise HostedAguiError(
                    "agui_parent_not_found",
                    "An initial AG-UI Run cannot select parentRunId.",
                    category=ErrorCategory.conflict,
                )
            await self._commands.runs.start(
                actor=actor,
                workspace_id=actor.workspace_id,
                idempotency_key=idempotency_key,
                request=StartRunCommand(
                    agent_id=agent_id,
                    agent_revision_id=mapped.agent_revision_id,
                    config_override=mapped.config_override,
                    input=_require_agui_input(mapped),
                    protocol_context=mapped.protocol_context,
                ),
                transaction_hook=bind,
            )
        else:
            assert latest is not None and selected_parent is not None
            assert source is not None and service_thread is not None
            if historical_parent:
                if mapped.resolutions is not None:
                    raise HostedAguiError(
                        "agui_resume_parent_invalid",
                        "AG-UI feedback can target only the active waiting Run.",
                        category=ErrorCategory.conflict,
                    )
                if source.status != RunStatus.completed.value:
                    raise HostedAguiError(
                        "agui_parent_not_forkable",
                        "The selected historical AG-UI parent Run is not completed.",
                        category=ErrorCategory.conflict,
                    )
                await self._commands.runs.fork(
                    actor=actor,
                    run_id=source.id,
                    idempotency_key=idempotency_key,
                    request=ForkRunCommand(
                        input=_require_agui_input(mapped),
                        protocol_context=mapped.protocol_context,
                        agent_id=agent_id,
                        agent_revision_id=mapped.agent_revision_id,
                        config_override=mapped.config_override,
                    ),
                    transaction_hook=bind,
                )
            elif source.status == RunStatus.waiting.value:
                if source.sealed_state_digest_sha256 is None:
                    raise HostedAguiError(
                        "agui_waiting_state_invalid",
                        "The active AG-UI Run has no complete waiting state.",
                        category=ErrorCategory.conflict,
                    )
                if mapped.resolutions is not None:
                    await self._commands.continuations.feedback(
                        actor=actor,
                        run_id=source.id,
                        idempotency_key=idempotency_key,
                        request=WaitingRunFeedbackRequest(
                            expected_thread_version=service_thread.version,
                            sealed_state_digest_sha256=source.sealed_state_digest_sha256,
                            resolutions=mapped.resolutions,
                        ),
                        transaction_hook=bind,
                        protocol_context=mapped.protocol_context,
                    )
                else:
                    await self._commands.continuations.continue_waiting(
                        actor=actor,
                        run_id=source.id,
                        idempotency_key=idempotency_key,
                        request=WaitingContinueRunCommand(
                            expected_thread_version=service_thread.version,
                            sealed_state_digest_sha256=source.sealed_state_digest_sha256,
                            input=_require_agui_input(mapped),
                            protocol_context=mapped.protocol_context,
                        ),
                        transaction_hook=bind,
                    )
            else:
                if mapped.resolutions is not None:
                    raise HostedAguiError(
                        "agui_run_not_waiting",
                        "AG-UI feedback requires the active Run to be waiting.",
                        category=ErrorCategory.conflict,
                    )
                await self._commands.runs.continue_from(
                    actor=actor,
                    source_run_id=source.id,
                    idempotency_key=idempotency_key,
                    request=ContinueRunCommand(
                        expected_thread_version=service_thread.version,
                        input=_require_agui_input(mapped),
                        protocol_context=mapped.protocol_context,
                        agent_id=agent_id,
                        agent_revision_id=mapped.agent_revision_id,
                        config_override=mapped.config_override,
                    ),
                    transaction_hook=bind,
                )

        bound = await self._load_run_binding(
            actor=actor,
            agent_id=agent_id,
            external_thread_id=request.thread_id,
            external_run_id=request.run_id,
        )
        if bound is None:
            raise HostedAguiError(
                "agui_binding_failed",
                "The accepted Run binding is unavailable.",
                category=ErrorCategory.unavailable,
            )
        if bound.request_digest_sha256 != request_digest:
            raise HostedAguiError(
                "agui_run_id_conflict",
                "The AG-UI runId was already used with different input.",
                category=ErrorCategory.conflict,
            )
        return await self._attachment(actor=actor, binding=bound, cursor=last_event_id)

    async def cancel(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        request: HostedAguiCancelRequest,
    ) -> HostedAguiCancelReceipt:
        _validate_external_id(request.thread_id, name="threadId")
        _validate_external_id(request.run_id, name="runId")
        binding = await self._load_run_binding(
            actor=actor,
            agent_id=agent_id,
            external_thread_id=request.thread_id,
            external_run_id=request.run_id,
        )
        if binding is None:
            raise HostedAguiError(
                "resource_not_found",
                "The requested resource was not found.",
                category=ErrorCategory.not_found,
            )
        await self._authorize_action(actor=actor, binding=binding, action=WorkspaceAction.run_interrupt)
        run, thread = await self._load_run_and_thread(binding)
        if run.status == RunStatus.cancelled.value:
            return HostedAguiCancelReceipt(threadId=request.thread_id, runId=request.run_id)
        if run.status not in {RunStatus.accepted.value, RunStatus.running.value}:
            raise HostedAguiError(
                "agui_run_not_interruptible",
                "The selected AG-UI Run is no longer active.",
                category=ErrorCategory.conflict,
            )
        key_material = f"{request.thread_id}\0{request.run_id}".encode()
        await self._commands.active.interrupt(
            actor=actor,
            run_id=binding.run_id,
            idempotency_key=f"agui-cancel-{hashlib.sha256(key_material).hexdigest()}",
            request=InterruptRequest(
                expected_run_version=run.version,
                expected_thread_version=thread.version,
            ),
        )
        return HostedAguiCancelReceipt(threadId=request.thread_id, runId=request.run_id)

    async def events(self, attachment: HostedAguiAttachment) -> AsyncIterator[bytes]:
        binding = attachment.binding
        try:
            retained = await self._sealed_replay(binding)
        except HostedAguiReplayUnavailable:
            retained = None
        except HostedAguiReplayError:
            gap = {
                "type": "CUSTOM",
                "name": "a13n.service.replay_gap",
                "value": {"schema_version": "1", "run_id": binding.external_run_id},
            }
            if attachment.after_ordinal < 0:
                yield _sse(binding, 0, gap)
            return
        if retained is not None:
            for item in retained.events:
                if item.ordinal > attachment.after_ordinal:
                    yield _sse(binding, item.ordinal, item.event)
            return
        ordinal = 0
        native_cursor: str | None = None
        started = monotonic()
        last_heartbeat = started
        last_authorized = started

        started_event: dict[str, Any] = {
            "type": "RUN_STARTED",
            "threadId": binding.external_thread_id,
            "runId": binding.external_run_id,
        }
        if ordinal > attachment.after_ordinal:
            yield _sse(binding, ordinal, started_event)
        ordinal += 1

        while monotonic() - started < self._maximum_lifetime_seconds:
            now = monotonic()
            if now - last_authorized >= self._authorization_interval_seconds:
                try:
                    await self._authorize_read(actor=attachment.actor, binding=binding)
                except HostedAguiError:
                    return
                last_authorized = now
            try:
                page = await self._stream.read(
                    binding.organization_id,
                    binding.run_id,
                    after_stream_id=native_cursor,
                    limit=self._page_size,
                )
                entries = page.items
                closed = page.closed and (page.next_stream_id == page.high_watermark or not page.items)
            except RunStreamReplayGap:
                try:
                    archived = await self._sealed_replay(binding)
                except HostedAguiReplayError:
                    archived = None
                if archived is not None:
                    for item in archived.events:
                        if item.ordinal >= ordinal and item.ordinal > attachment.after_ordinal:
                            yield _sse(binding, item.ordinal, item.event)
                else:
                    gap = {
                        "type": "CUSTOM",
                        "name": "a13n.service.replay_gap",
                        "value": {"schema_version": "1", "run_id": binding.external_run_id},
                    }
                    if ordinal > attachment.after_ordinal:
                        yield _sse(binding, ordinal, gap)
                return
            for entry in entries:
                native_cursor = entry.stream_id
                projected = _project_event(entry, external_run_id=binding.external_run_id)
                if projected is None:
                    continue
                if ordinal > attachment.after_ordinal:
                    yield _sse(binding, ordinal, projected)
                    last_heartbeat = monotonic()
                ordinal += 1
            run = await self._run_record(binding)
            status = RunStatus(run.status)
            sealed = status in {RunStatus.waiting, RunStatus.completed, RunStatus.failed, RunStatus.cancelled}
            if sealed and status is not RunStatus.waiting and not closed:
                await anyio.sleep(self._poll_interval_seconds)
                continue
            if closed or sealed:
                if sealed:
                    try:
                        await self._sealed_replay(binding)
                    except HostedAguiReplayError:
                        pass
                terminal = _terminal_event(binding, run)
                if terminal is not None and ordinal > attachment.after_ordinal:
                    yield _sse(binding, ordinal, terminal)
                return
            now = monotonic()
            if now - last_heartbeat >= self._heartbeat_interval_seconds:
                yield b": heartbeat\n\n"
                last_heartbeat = now
            await anyio.sleep(self._poll_interval_seconds)

    async def _attachment(
        self,
        *,
        actor: AuthenticatedActor,
        binding: HostedAguiBinding,
        cursor: str | None,
    ) -> HostedAguiAttachment:
        after_ordinal = _resume_ordinal(binding, cursor)
        try:
            retained = await self._sealed_replay(binding)
        except HostedAguiReplayUnavailable as error:
            if cursor is not None:
                # Sealing the Run precedes presentation closure/publication. A
                # missing immutable snapshot is not a gap while the full live
                # prefix is still available for the same ordered projection.
                try:
                    await self._stream.untrimmed_entries(binding.organization_id, binding.run_id)
                except RunStreamError:
                    raise HostedAguiError(
                        "agui_replay_gap",
                        "The requested Hosted AG-UI delivery history is unavailable.",
                        category=ErrorCategory.conflict,
                    ) from error
            retained = None
        except HostedAguiReplayError as error:
            raise HostedAguiError(
                "agui_replay_unavailable",
                "Hosted AG-UI delivery history is temporarily unavailable.",
                category=ErrorCategory.unavailable,
            ) from error
        if retained is not None and after_ordinal >= len(retained.events):
            raise HostedAguiError(
                "agui_cursor_invalid",
                "The Hosted AG-UI cursor is outside the retained delivery history.",
                category=ErrorCategory.conflict,
            )
        return HostedAguiAttachment(actor, binding, after_ordinal)

    async def _sealed_replay(self, binding: HostedAguiBinding) -> HostedAguiReplaySnapshot | None:
        try:
            retained = await self._hosted_replay.read(binding.organization_id, binding.run_binding_id)
        except ObjectNotFound:
            pass
        else:
            _validate_replay_binding(binding, retained)
            return retained
        run = await self._run_record(binding)
        if run.status not in {
            RunStatus.waiting.value,
            RunStatus.completed.value,
            RunStatus.failed.value,
            RunStatus.cancelled.value,
        }:
            return None
        entries = await self._complete_projection_source(
            binding,
            waiting=run.status == RunStatus.waiting.value,
        )
        snapshot = _build_replay_snapshot(binding, run, entries)
        retained = await self._hosted_replay.publish(binding.organization_id, snapshot)
        _validate_replay_binding(binding, retained)
        return retained

    async def _complete_projection_source(
        self,
        binding: HostedAguiBinding,
        *,
        waiting: bool,
    ) -> tuple[RunStreamEntry, ...]:
        try:
            if waiting:
                entries = await self._stream.untrimmed_entries(binding.organization_id, binding.run_id)
            else:
                source = await self._stream.complete_source(binding.organization_id, binding.run_id)
                entries = source.entries
        except RunStreamError as error:
            raise HostedAguiReplayUnavailable("The complete Native presentation source is unavailable") from error
        if not entries:
            raise HostedAguiReplayUnavailable("The complete Native presentation source is unavailable")
        return entries

    async def _validate_and_map_input(
        self,
        request: RunAgentInput,
        *,
        request_json: dict[str, JsonValue],
        thread: AguiThreadBindingRecord | None,
        history: AguiRunBindingRecord | None,
        revision_id: str,
        config: AgentConfig,
        reuse_waiting_surface: bool,
    ) -> _MappedAguiInput:
        _validate_request_size(request_json, maximum=config.protocol.limits.max_input_bytes)
        _validate_protocol_value(
            request.state,
            schema=config.protocol.state_schema,
            empty_is_valid=True,
            name="state",
        )
        context = [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in request.context]
        protocol_context = ProtocolInputContext.model_validate({"state": request.state, "context": context})
        _validate_protocol_value(
            context,
            schema=config.protocol.context_schema,
            empty_is_valid=True,
            name="context",
        )
        client_tools = _validated_client_tools(request, config=config)
        if reuse_waiting_surface:
            if history is None or _request_tools(request) != history.request_json.get("tools", []):
                raise HostedAguiError(
                    "agui_tool_surface_changed",
                    "A waiting AG-UI Run must reuse its exact client tool surface.",
                    category=ErrorCategory.conflict,
                )
            config_override = None
        else:
            config_override = AgentRunOverride(client_tools=client_tools)
        if request.resume is not None:
            raise HostedAguiError(
                "agui_resume_not_supported",
                "The AG-UI resume extension is not available for this Run.",
                category=ErrorCategory.conflict,
            )
        resolutions = _forwarded_resolutions(request.forwarded_props)
        tool_resolution = _tool_message_resolution(request.messages[-1]) if request.messages else None
        if resolutions is not None and tool_resolution is not None:
            raise HostedAguiError(
                "agui_feedback_ambiguous",
                "A tool result and forwardedProps.a13n.resume cannot be submitted together.",
                category=ErrorCategory.invalid_request,
            )
        if resolutions is not None:
            if thread is None or history is None:
                raise HostedAguiError(
                    "agui_resume_without_binding",
                    "AG-UI feedback requires an existing waiting Run.",
                    category=ErrorCategory.conflict,
                )
            expected = await self._expected_messages(history)
            supplied = tuple(_message_json(item) for item in request.messages)
            if supplied != expected:
                raise HostedAguiError(
                    "agui_history_conflict",
                    "The AG-UI message snapshot does not match the authorized history.",
                    category=ErrorCategory.conflict,
                )
            return _MappedAguiInput(
                input=None,
                resolutions=resolutions,
                agent_revision_id=revision_id,
                config_override=config_override,
                protocol_context=protocol_context,
            )
        if tool_resolution is not None:
            if thread is None or history is None:
                raise HostedAguiError(
                    "agui_feedback_without_binding",
                    "An AG-UI tool result requires an existing waiting Run.",
                    category=ErrorCategory.conflict,
                )
            expected = await self._expected_messages(history)
            supplied = tuple(_message_json(item) for item in request.messages[:-1])
            if supplied != expected:
                raise HostedAguiError(
                    "agui_history_conflict",
                    "The AG-UI message snapshot does not match the authorized history.",
                    category=ErrorCategory.conflict,
                )
            return _MappedAguiInput(
                input=None,
                resolutions=(tool_resolution,),
                agent_revision_id=revision_id,
                config_override=config_override,
                protocol_context=protocol_context,
            )
        if not request.messages or not isinstance(request.messages[-1], UserMessage):
            raise HostedAguiError(
                "agui_input_invalid",
                "AG-UI input must append one user message or one waiting tool result.",
                category=ErrorCategory.invalid_request,
            )
        if thread is None:
            if len(request.messages) != 1:
                raise HostedAguiError(
                    "agui_history_import_forbidden",
                    "An initial AG-UI Run cannot import prior message history.",
                    category=ErrorCategory.invalid_request,
                )
        else:
            if history is None:
                raise HostedAguiError(
                    "agui_binding_invalid", "The AG-UI binding has no Run.", category=ErrorCategory.conflict
                )
            expected = await self._expected_messages(history)
            supplied = tuple(_message_json(item) for item in request.messages[:-1])
            if supplied != expected:
                raise HostedAguiError(
                    "agui_history_conflict",
                    "The AG-UI message snapshot does not match the authorized history.",
                    category=ErrorCategory.conflict,
                )
        return _MappedAguiInput(
            input=_user_input(request.messages[-1]),
            resolutions=None,
            agent_revision_id=revision_id,
            config_override=config_override,
            protocol_context=protocol_context,
        )

    async def _load_protocol_revision(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        revision_id: str | None,
        authorize_invoke: bool,
        trusted_organization_id: str | None,
    ) -> tuple[str, AgentConfig]:
        async with short_session(self._sessions) as database:
            organization_id = trusted_organization_id
            if authorize_invoke:
                try:
                    authorized = await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.workspace_id,
                        agent_id=agent_id,
                        action=WorkspaceAction.agent_invoke,
                    )
                except AuthorizationError as error:
                    raise HostedAguiError(
                        "resource_not_found",
                        "The requested resource was not found.",
                        category=ErrorCategory.not_found,
                    ) from error
                organization_id = authorized.organization_id
            if organization_id is None:
                raise HostedAguiError(
                    "resource_not_found",
                    "The requested resource was not found.",
                    category=ErrorCategory.not_found,
                )
            agent = await database.scalar(
                select(AgentRecord).where(
                    AgentRecord.id == agent_id,
                    AgentRecord.workspace_id == actor.workspace_id,
                    AgentRecord.organization_id == organization_id,
                )
            )
            if agent is None:
                raise HostedAguiError(
                    "resource_not_found",
                    "The requested resource was not found.",
                    category=ErrorCategory.not_found,
                )
            selected_revision_id = revision_id or agent.current_revision_id
            revision = await database.scalar(
                select(AgentRevisionRecord).where(
                    AgentRevisionRecord.id == selected_revision_id,
                    AgentRevisionRecord.agent_id == agent_id,
                    AgentRevisionRecord.organization_id == agent.organization_id,
                    AgentRevisionRecord.workspace_id == actor.workspace_id,
                )
            )
        if revision is None:
            raise HostedAguiError(
                "resource_not_found",
                "The requested resource was not found.",
                category=ErrorCategory.not_found,
            )
        return revision.id, AgentConfig.model_validate(revision.config)

    async def _expected_messages(self, latest: AguiRunBindingRecord) -> tuple[dict[str, JsonValue], ...]:
        raw_messages = latest.request_json.get("messages")
        if not isinstance(raw_messages, list):
            raise HostedAguiError(
                "agui_binding_invalid", "The AG-UI message snapshot is invalid.", category=ErrorCategory.conflict
            )
        prefix = tuple(_JSON_OBJECT.validate_python(item, strict=True) for item in raw_messages)
        entries = await self._all_entries(latest.organization_id, latest.run_id)
        return prefix + _messages_from_entries(entries)

    async def _all_entries(self, organization_id: str, run_id: str) -> tuple[RunStreamEntry, ...]:
        cursor: str | None = None
        values: list[RunStreamEntry] = []
        try:
            while True:
                page = await self._stream.read(
                    organization_id,
                    run_id,
                    after_stream_id=cursor,
                    limit=self._page_size,
                )
                values.extend(page.items)
                if not page.items:
                    break
                cursor = page.items[-1].stream_id
                if page.closed and cursor == page.high_watermark:
                    break
        except RunStreamReplayGap as error:
            raise HostedAguiError(
                "agui_history_unavailable",
                "The retained AG-UI message history is unavailable.",
                category=ErrorCategory.conflict,
            ) from error
        return tuple(values)

    async def _load_thread_binding(
        self, *, actor: AuthenticatedActor, agent_id: str, external_thread_id: str
    ) -> AguiThreadBindingRecord | None:
        async with short_session(self._sessions) as database:
            return await database.scalar(
                select(AguiThreadBindingRecord).where(
                    AguiThreadBindingRecord.workspace_id == actor.workspace_id,
                    AguiThreadBindingRecord.client_principal_type == actor.principal.principal_type.value,
                    AguiThreadBindingRecord.client_principal_id == actor.principal.principal_id,
                    AguiThreadBindingRecord.agent_id == agent_id,
                    AguiThreadBindingRecord.external_thread_id == external_thread_id,
                )
            )

    async def _load_run_binding(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        external_thread_id: str,
        external_run_id: str,
    ) -> HostedAguiBinding | None:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(AguiRunBindingRecord, AguiThreadBindingRecord)
                    .join(
                        AguiThreadBindingRecord,
                        AguiThreadBindingRecord.id == AguiRunBindingRecord.thread_binding_id,
                    )
                    .where(
                        AguiThreadBindingRecord.workspace_id == actor.workspace_id,
                        AguiThreadBindingRecord.client_principal_type == actor.principal.principal_type.value,
                        AguiThreadBindingRecord.client_principal_id == actor.principal.principal_id,
                        AguiRunBindingRecord.agent_id == agent_id,
                        AguiRunBindingRecord.external_thread_id == external_thread_id,
                        AguiRunBindingRecord.external_run_id == external_run_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        run, thread = row
        return _binding(run, thread)

    async def _load_latest_run(self, thread_binding_id: str) -> AguiRunBindingRecord | None:
        async with short_session(self._sessions) as database:
            return await database.scalar(
                select(AguiRunBindingRecord)
                .join(
                    AguiThreadBindingRecord,
                    AguiThreadBindingRecord.id == AguiRunBindingRecord.thread_binding_id,
                )
                .join(
                    ThreadRecord,
                    and_(
                        ThreadRecord.id == AguiThreadBindingRecord.active_thread_id,
                        ThreadRecord.current_run_id == AguiRunBindingRecord.run_id,
                    ),
                )
                .where(AguiRunBindingRecord.thread_binding_id == thread_binding_id)
            )

    async def _load_thread_run_binding(
        self,
        thread_binding_id: str,
        external_run_id: str,
    ) -> AguiRunBindingRecord | None:
        async with short_session(self._sessions) as database:
            return await database.scalar(
                select(AguiRunBindingRecord).where(
                    AguiRunBindingRecord.thread_binding_id == thread_binding_id,
                    AguiRunBindingRecord.external_run_id == external_run_id,
                )
            )

    async def _load_continuation_source(
        self,
        run_id: str,
        thread_id: str | None,
    ) -> tuple[RunRecord, ThreadRecord]:
        async with short_session(self._sessions) as database:
            predicates = [RunRecord.id == run_id]
            if thread_id is not None:
                predicates.append(RunRecord.thread_id == thread_id)
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(*predicates)
                )
            ).one_or_none()
        if row is None or row[0].status not in {RunStatus.completed.value, RunStatus.waiting.value}:
            raise HostedAguiError(
                "agui_run_not_continuable",
                "The active AG-UI Run is neither completed nor waiting.",
                category=ErrorCategory.conflict,
            )
        return row[0], row[1]

    async def _authorize_read(self, *, actor: AuthenticatedActor, binding: HostedAguiBinding) -> None:
        await self._authorize_action(actor=actor, binding=binding, action=WorkspaceAction.run_read)

    async def _authorize_action(
        self,
        *,
        actor: AuthenticatedActor,
        binding: HostedAguiBinding,
        action: WorkspaceAction,
    ) -> None:
        async with short_session(self._sessions) as database:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=binding.workspace_id,
                    agent_id=binding.agent_id,
                    action=action,
                )
            except AuthorizationError as error:
                raise HostedAguiError(
                    "resource_not_found",
                    "The requested resource was not found.",
                    category=ErrorCategory.not_found,
                ) from error

    async def _load_run_and_thread(self, binding: HostedAguiBinding) -> tuple[RunRecord, ThreadRecord]:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.organization_id == binding.organization_id,
                        RunRecord.id == binding.run_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise HostedAguiError(
                "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
            )
        return row[0], row[1]

    async def _run_record(self, binding: HostedAguiBinding) -> RunRecord:
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(RunRecord).where(
                    RunRecord.organization_id == binding.organization_id,
                    RunRecord.id == binding.run_id,
                )
            )
        if record is None:
            raise HostedAguiError(
                "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
            )
        return record


def _binding(run: AguiRunBindingRecord, thread: AguiThreadBindingRecord) -> HostedAguiBinding:
    return HostedAguiBinding(
        organization_id=run.organization_id,
        workspace_id=run.workspace_id,
        thread_binding_id=thread.id,
        run_binding_id=run.id,
        agent_id=run.agent_id,
        agent_revision_id=run.agent_revision_id,
        external_thread_id=run.external_thread_id,
        external_run_id=run.external_run_id,
        run_id=run.run_id,
        request_digest_sha256=run.request_digest_sha256,
    )


def _validate_replay_binding(binding: HostedAguiBinding, replay: HostedAguiReplaySnapshot) -> None:
    if (
        replay.binding_id != binding.run_binding_id
        or replay.run_id != binding.run_id
        or replay.external_thread_id != binding.external_thread_id
        or replay.external_run_id != binding.external_run_id
        or replay.agent_revision_id != binding.agent_revision_id
    ):
        raise HostedAguiReplayError("Hosted AG-UI replay correlation does not match its binding")


def _build_replay_snapshot(
    binding: HostedAguiBinding,
    run: RunRecord,
    entries: Sequence[RunStreamEntry],
) -> HostedAguiReplaySnapshot:
    events: list[dict[str, Any]] = [
        _standard_event(
            {
                "type": "RUN_STARTED",
                "threadId": binding.external_thread_id,
                "runId": binding.external_run_id,
            }
        )
    ]
    events.extend(
        projected
        for entry in entries
        if (projected := _project_event(entry, external_run_id=binding.external_run_id)) is not None
    )
    terminal = _terminal_event(binding, run)
    if terminal is None:
        raise HostedAguiReplayUnavailable("The Hosted AG-UI Run has no sealed delivery boundary")
    events.append(terminal)
    return HostedAguiReplaySnapshot(
        binding_id=binding.run_binding_id,
        run_id=binding.run_id,
        external_thread_id=binding.external_thread_id,
        external_run_id=binding.external_run_id,
        agent_revision_id=binding.agent_revision_id,
        sealed_at=assume_utc(run.sealed_at or run.updated_at),
        events=tuple(HostedAguiDeliveryEvent(ordinal=ordinal, event=event) for ordinal, event in enumerate(events)),
    )


def _validate_external_id(value: str, *, name: str) -> None:
    if not 1 <= len(value.encode("utf-8")) <= 512 or "\x00" in value:
        raise HostedAguiError("agui_id_invalid", f"{name} is invalid.", category=ErrorCategory.invalid_request)


def _empty(value: object) -> bool:
    return value is None or value == {} or value == []


def _validate_request_size(value: dict[str, JsonValue], *, maximum: int) -> None:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > maximum:
        raise HostedAguiError(
            "agui_input_too_large",
            "The AG-UI input exceeds this Agent's configured limit.",
            category=ErrorCategory.size_limit,
        )


def _validate_protocol_value(
    value: object,
    *,
    schema: dict[str, JsonValue] | None,
    empty_is_valid: bool,
    name: Literal["state", "context"],
) -> None:
    if empty_is_valid and _empty(value):
        return
    try:
        json_value = _JSON_VALUE.validate_python(value, strict=True)
    except ValueError as error:
        raise HostedAguiError(
            f"agui_{name}_invalid",
            f"AG-UI {name} is not a valid JSON value.",
            category=ErrorCategory.invalid_request,
        ) from error
    if schema is None:
        raise HostedAguiError(
            f"agui_{name}_not_allowed",
            f"This Agent does not accept AG-UI {name}.",
            category=ErrorCategory.invalid_request,
        )
    if not Draft202012Validator(schema).is_valid(json_value):
        raise HostedAguiError(
            f"agui_{name}_invalid",
            f"AG-UI {name} does not match the selected Agent Revision schema.",
            category=ErrorCategory.invalid_request,
        )


def _validated_client_tools(
    request: RunAgentInput,
    *,
    config: AgentConfig,
) -> tuple[ClientToolDefinition, ...]:
    policies = {item.name: item for item in config.protocol.client_tools}
    definitions = {item.name: item for item in config.client_tools}
    supplied_names = tuple(item.name for item in request.tools)
    if len(supplied_names) != len(set(supplied_names)):
        raise HostedAguiError(
            "agui_tools_invalid",
            "AG-UI client tool names must be unique.",
            category=ErrorCategory.invalid_request,
        )
    unknown = set(supplied_names) - set(policies)
    missing = {name for name, policy in policies.items() if policy.required} - set(supplied_names)
    if unknown or missing:
        raise HostedAguiError(
            "agui_tools_not_allowed",
            "AG-UI client tools do not match the selected Agent Revision policy.",
            category=ErrorCategory.invalid_request,
        )

    normalized: list[ClientToolDefinition] = []
    for tool in request.tools:
        if tool.model_extra:
            raise HostedAguiError(
                "agui_tools_invalid",
                "AG-UI client tool declarations contain unsupported fields.",
                category=ErrorCategory.invalid_request,
            )
        declared = definitions.get(tool.name)
        if (
            declared is None
            or tool.description != declared.description
            or tool.parameters != declared.parameters_json_schema
        ):
            raise HostedAguiError(
                "agui_tools_not_allowed",
                "AG-UI client tools do not match the selected Agent Revision policy.",
                category=ErrorCategory.invalid_request,
            )
        normalized.append(declared)
    return tuple(normalized)


def _request_tools(request: RunAgentInput) -> list[dict[str, JsonValue]]:
    return [
        _JSON_OBJECT.validate_python(
            item.model_dump(mode="json", by_alias=True, exclude_none=True),
            strict=True,
        )
        for item in request.tools
    ]


def _forwarded_resolutions(value: object) -> tuple[SubmittedPendingResolution, ...] | None:
    if _empty(value):
        return None
    try:
        forwarded = _HostedForwardedProps.model_validate(value)
        return _RESOLUTIONS.validate_python(forwarded.a13n.resume)
    except ValueError as error:
        raise HostedAguiError(
            "agui_extension_invalid",
            "forwardedProps.a13n is invalid.",
            category=ErrorCategory.invalid_request,
        ) from error


def _has_feedback_input(request: RunAgentInput) -> bool:
    return _forwarded_resolutions(request.forwarded_props) is not None or bool(
        request.messages and isinstance(request.messages[-1], ToolMessage)
    )


def _tool_message_resolution(message: object) -> CompletePendingResolution | None:
    if not isinstance(message, ToolMessage):
        return None
    if message.error is not None or message.encrypted_value is not None:
        raise HostedAguiError(
            "agui_tool_result_invalid",
            "AG-UI tool results cannot contain error or encryptedValue fields.",
            category=ErrorCategory.invalid_request,
        )
    try:
        return CompletePendingResolution(call_id=message.tool_call_id, result=message.content)
    except ValueError as error:
        raise HostedAguiError(
            "agui_tool_result_invalid",
            "The AG-UI tool result is invalid.",
            category=ErrorCategory.invalid_request,
        ) from error


def _require_agui_input(mapped: _MappedAguiInput) -> AgentInput:
    if mapped.input is None:
        raise HostedAguiError(
            "agui_input_required",
            "This AG-UI operation requires a new user input tail.",
            category=ErrorCategory.invalid_request,
        )
    return mapped.input


def _user_input(message: UserMessage) -> AgentInput:
    if message.name is not None or message.encrypted_value is not None:
        raise HostedAguiError(
            "agui_input_invalid", "Unsupported user message metadata.", category=ErrorCategory.invalid_request
        )
    content = message.content
    blocks: list[dict[str, Any]] = []
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
    else:
        for part in content:
            if part.type == "text":
                blocks.append({"type": "text", "text": part.text})
                continue
            if isinstance(part, BinaryInputContent):
                if part.url is None or part.data is not None:
                    raise HostedAguiError(
                        "agui_binary_source_unsupported",
                        "Inline AG-UI binary data is not supported; use an authorized URL.",
                        category=ErrorCategory.invalid_request,
                    )
                blocks.append(
                    {
                        "type": "binary",
                        "source": {"type": "url", "url": part.url},
                        "filename": part.filename,
                        "media_type": part.mime_type,
                    }
                )
                continue
            source = part.source
            if source.type != "url":
                raise HostedAguiError(
                    "agui_binary_source_unsupported",
                    "Inline AG-UI binary data is not supported; use an authorized URL.",
                    category=ErrorCategory.invalid_request,
                )
            metadata = part.metadata if isinstance(part.metadata, dict) else {}
            blocks.append(
                {
                    "type": "binary",
                    "source": {"type": "url", "url": source.value},
                    "filename": metadata.get("filename"),
                    "media_type": source.mime_type,
                }
            )
    if not blocks:
        raise HostedAguiError(
            "agui_input_invalid", "The user message is empty.", category=ErrorCategory.invalid_request
        )
    try:
        return AgentInput.model_validate({"schema_version": "2", "content": blocks})
    except ValueError as error:
        raise HostedAguiError(
            "agui_input_invalid", "The user message is invalid.", category=ErrorCategory.invalid_request
        ) from error


def _message_json(message: BaseModel) -> dict[str, JsonValue]:
    return _JSON_OBJECT.validate_python(
        message.model_dump(mode="json", by_alias=True, exclude_none=True),
        strict=True,
    )


def _messages_from_entries(entries: Sequence[RunStreamEntry]) -> tuple[dict[str, JsonValue], ...]:
    messages: list[dict[str, JsonValue]] = []
    text_messages: dict[str, dict[str, Any]] = {}
    tool_calls: dict[str, dict[str, Any]] = {}
    for entry in entries:
        projected = _project_observation(entry)
        if projected is None:
            continue
        event_type = projected["type"]
        if event_type == "TEXT_MESSAGE_START":
            message_id = projected.get("messageId")
            if isinstance(message_id, str):
                text_messages[message_id] = {
                    "id": message_id,
                    "role": projected.get("role", "assistant"),
                    "content": "",
                }
        elif event_type == "TEXT_MESSAGE_CONTENT":
            message_id = projected.get("messageId")
            delta = projected.get("delta")
            if isinstance(message_id, str) and isinstance(delta, str) and message_id in text_messages:
                text_messages[message_id]["content"] += delta
        elif event_type == "TEXT_MESSAGE_END":
            message_id = projected.get("messageId")
            if isinstance(message_id, str) and message_id in text_messages:
                messages.append(_message_json(_MESSAGE.validate_python(text_messages.pop(message_id))))
        elif event_type == "TOOL_CALL_START":
            call_id = projected.get("toolCallId")
            if isinstance(call_id, str):
                tool_calls[call_id] = {
                    "id": call_id,
                    "name": projected.get("toolCallName", "tool"),
                    "arguments": "",
                    "parent": projected.get("parentMessageId"),
                }
        elif event_type == "TOOL_CALL_ARGS":
            call_id = projected.get("toolCallId")
            delta = projected.get("delta")
            if isinstance(call_id, str) and isinstance(delta, str) and call_id in tool_calls:
                tool_calls[call_id]["arguments"] += delta
        elif event_type == "TOOL_CALL_END":
            call_id = projected.get("toolCallId")
            call = tool_calls.get(call_id) if isinstance(call_id, str) else None
            if call is not None:
                parent = call.pop("parent")
                value = ToolCall.model_validate(
                    {
                        "id": call["id"],
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                )
                if isinstance(parent, str) and parent in text_messages:
                    text_messages[parent].setdefault("toolCalls", []).append(
                        value.model_dump(mode="json", by_alias=True)
                    )
                else:
                    messages.append(
                        _message_json(
                            AssistantMessage(
                                id=parent if isinstance(parent, str) else f"assistant-{call_id}",
                                content=None,
                                tool_calls=[value],
                            )
                        )
                    )
        elif event_type == "TOOL_CALL_RESULT":
            call_id = projected.get("toolCallId")
            message_id = projected.get("messageId")
            content = projected.get("content")
            if isinstance(call_id, str) and isinstance(message_id, str) and isinstance(content, str):
                messages.append(_message_json(ToolMessage(id=message_id, tool_call_id=call_id, content=content)))
    return tuple(messages)


def _project_event(entry: RunStreamEntry, *, external_run_id: str) -> dict[str, Any] | None:
    if entry.event.event_type == "run.recovery":
        payload = RecoveryPayload.model_validate(entry.event.payload)
        return _standard_event(
            {
                "type": "CUSTOM",
                "name": "a13n.service.run_recovery",
                "value": {
                    "schema_version": "1",
                    "event_id": entry.event.event_id,
                    "runId": external_run_id,
                    "reason": payload.reason,
                },
            }
        )
    return _project_observation(entry)


def _project_observation(entry: RunStreamEntry) -> dict[str, Any] | None:
    prefix, separator, name = entry.event.event_type.partition(".")
    if prefix != "agui" or not separator or name not in _VISIBLE_EVENTS:
        return None
    metadata = entry.event.payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("display") is False:
        return None
    event = _standard_event({"type": name.upper(), **entry.event.payload})
    if name == "tool_call_start" and not event.get("parentMessageId"):
        # Tool-only assistant messages still need an identity a client can use
        # in its next compatibility snapshot. Publish that association on wire.
        event["parentMessageId"] = f"assistant-{event['toolCallId']}"
    # Observer message identities can contain a private Harness Run ID. Apply the
    # same stable presentation mapping for live delivery, replay, and snapshots.
    for field in ("messageId", "parentMessageId"):
        identity = event.get(field)
        if isinstance(identity, str):
            event[field] = "msg_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    return event


def _terminal_event(binding: HostedAguiBinding, run: RunRecord) -> dict[str, Any] | None:
    status = RunStatus(run.status)
    if status is RunStatus.waiting:
        return _standard_event(
            {
                "type": "CUSTOM",
                "name": "a13n.service.run_status",
                "value": {
                    "schema_version": "1",
                    "status": "waiting",
                    "run_id": binding.external_run_id,
                    "pending": run.pending_json,
                },
            }
        )
    if status is RunStatus.completed:
        return _standard_event(
            {"type": "RUN_FINISHED", "threadId": binding.external_thread_id, "runId": binding.external_run_id}
        )
    if status is RunStatus.failed:
        failure = run.failure_json or {}
        code = failure.get("code")
        message = failure.get("message")
        return _standard_event(
            {
                "type": "RUN_ERROR",
                "code": code if isinstance(code, str) else "run_failed",
                "message": message if isinstance(message, str) else "The Agent Run failed.",
            }
        )
    if status is RunStatus.cancelled:
        return _standard_event(
            {
                "type": "RUN_ERROR",
                "code": "run_cancelled",
                "message": "The Agent Run was cancelled.",
            }
        )
    return None


def _standard_event(value: dict[str, Any]) -> dict[str, Any]:
    event = _EVENT.validate_python(value)
    excluded = {"raw_event", *(event.model_extra or {}).keys()}
    return event.model_dump(mode="json", by_alias=True, exclude_none=True, exclude=excluded)


def _cursor(binding: HostedAguiBinding, ordinal: int) -> str:
    scope = hashlib.sha256(
        f"{binding.thread_binding_id}\0{binding.external_run_id}\0{binding.run_id}".encode()
    ).hexdigest()[:24]
    return f"hagui_{scope}_{ordinal}"


def _resume_ordinal(binding: HostedAguiBinding, cursor: str | None) -> int:
    if cursor is None:
        return -1
    prefix = _cursor(binding, 0).rsplit("_", maxsplit=1)[0] + "_"
    if not cursor.startswith(prefix):
        raise HostedAguiError(
            "agui_cursor_invalid", "The Hosted AG-UI cursor is invalid.", category=ErrorCategory.invalid_request
        )
    try:
        value = int(cursor.removeprefix(prefix))
    except ValueError as error:
        raise HostedAguiError(
            "agui_cursor_invalid", "The Hosted AG-UI cursor is invalid.", category=ErrorCategory.invalid_request
        ) from error
    if value < 0:
        raise HostedAguiError(
            "agui_cursor_invalid", "The Hosted AG-UI cursor is invalid.", category=ErrorCategory.invalid_request
        )
    return value


def _sse(binding: HostedAguiBinding, ordinal: int, event: dict[str, Any]) -> bytes:
    data = json.dumps(_standard_event(event), separators=(",", ":"), ensure_ascii=False)
    return f"id: {_cursor(binding, ordinal)}\ndata: {data}\n\n".encode()


__all__ = [
    "HostedAguiAttachment",
    "HostedAguiBinding",
    "HostedAguiCancelReceipt",
    "HostedAguiCancelRequest",
    "HostedAguiError",
    "HostedAguiService",
    "HostedAguiTerminalProjector",
]


@dataclass(frozen=True, slots=True)
class _PreparedAguiBinding:
    actor: AuthenticatedActor
    agent_id: str
    request: RunAgentInput
    request_json: dict[str, JsonValue]
    request_digest: str
    thread: AguiThreadBindingRecord | None
    thread_binding_id: str
    run_binding_id: str
    selected_parent: AguiRunBindingRecord | None
    now: datetime


async def _persist_agui_binding(
    database: AsyncSession, receipt: RunAcceptanceReceipt, *, prepared: _PreparedAguiBinding
) -> None:
    run = await database.scalar(
        select(RunRecord).where(RunRecord.id == receipt.run_id, RunRecord.thread_id == receipt.thread_id)
    )
    if run is None:
        raise HostedAguiError(
            "agui_binding_failed",
            "The accepted Run could not be bound.",
            category=ErrorCategory.conflict,
        )
    if prepared.thread is None:
        database.add(
            AguiThreadBindingRecord(
                id=prepared.thread_binding_id,
                organization_id=run.organization_id,
                workspace_id=prepared.actor.workspace_id,
                client_principal_type=prepared.actor.principal.principal_type.value,
                client_principal_id=prepared.actor.principal.principal_id,
                agent_id=prepared.agent_id,
                external_thread_id=prepared.request.thread_id,
                session_id=receipt.session_id,
                root_thread_id=receipt.thread_id,
                active_thread_id=receipt.thread_id,
                version=1,
                created_at=prepared.now,
                updated_at=prepared.now,
            )
        )
        await database.flush()
    else:
        selected = await database.scalar(
            select(AguiThreadBindingRecord).where(AguiThreadBindingRecord.id == prepared.thread.id).with_for_update()
        )
        if (
            selected is None
            or selected.version != prepared.thread.version
            or selected.active_thread_id != prepared.thread.active_thread_id
        ):
            raise HostedAguiError(
                "agui_thread_changed",
                "The AG-UI prepared.thread binding changed before Run acceptance.",
                category=ErrorCategory.conflict,
            )
        selected.version += 1
        selected.active_thread_id = receipt.thread_id
        selected.updated_at = prepared.now
    database.add(
        AguiRunBindingRecord(
            id=prepared.run_binding_id,
            organization_id=run.organization_id,
            workspace_id=prepared.actor.workspace_id,
            thread_binding_id=prepared.thread_binding_id,
            agent_id=prepared.agent_id,
            agent_revision_id=run.agent_revision_id,
            external_thread_id=prepared.request.thread_id,
            external_run_id=prepared.request.run_id,
            parent_external_run_id=None
            if prepared.selected_parent is None
            else prepared.selected_parent.external_run_id,
            request_digest_sha256=prepared.request_digest,
            request_json=prepared.request_json,
            run_id=receipt.run_id,
            created_at=prepared.now,
        )
    )
