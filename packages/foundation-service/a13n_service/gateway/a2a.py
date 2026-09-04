"""A2A 1.0 adapter over the shared Foundation interaction commands."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import datetime
from time import monotonic
from typing import Any

import anyio
from a2a.types import a2a_pb2 as a2a
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Value
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentConfig, canonical_digest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.ids import new_object_id
from a13n_service.interactions import AgentInput, InterruptRequest, RunAcceptanceReceipt, RunStatus
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.public_errors import PublicError
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .commands import ContinueRunRequest, NativeInteractionCommands, StartRunRequest, WaitingContinueRunRequest
from .models import A2AContextBindingRecord, A2AMessageBindingRecord, A2ATaskBindingRecord

_TERMINAL = frozenset({RunStatus.completed.value, RunStatus.failed.value, RunStatus.cancelled.value})


class A2AError(PublicError):
    """A bounded A2A adapter failure safe for the public binding."""


class A2AService:
    """Persist A2A identities and project their work through Native commands."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        commands: NativeInteractionCommands,
        *,
        poll_interval_seconds: float,
        maximum_wait_seconds: float,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._commands = commands
        self._poll_interval_seconds = poll_interval_seconds
        self._maximum_wait_seconds = maximum_wait_seconds
        self._clock = clock

    async def public_agent_card(self, *, agent_id: str, base_url: str) -> a2a.AgentCard:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(AgentRecord, AgentRevisionRecord)
                    .join(
                        AgentRevisionRecord,
                        and_(
                            AgentRevisionRecord.id == AgentRecord.current_revision_id,
                            AgentRevisionRecord.agent_id == AgentRecord.id,
                            AgentRevisionRecord.organization_id == AgentRecord.organization_id,
                            AgentRevisionRecord.workspace_id == AgentRecord.workspace_id,
                        ),
                    )
                    .where(
                        AgentRecord.id == agent_id,
                        AgentRecord.enabled.is_(True),
                        AgentRecord.archived_at.is_(None),
                    )
                )
            ).one_or_none()
        if row is None:
            raise _not_found()
        _agent, revision = row
        config = AgentConfig.model_validate(revision.config)
        protocol = config.protocol
        interface = a2a.AgentInterface(
            url=f"{base_url.rstrip('/')}/a2a/v1/agents/{agent_id}",
            protocol_binding="HTTP+JSON",
            protocol_version="1.0",
        )
        capabilities = a2a.AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=bool(protocol.extended_agent_card and protocol.extended_agent_card.enabled),
        )
        card = a2a.AgentCard(
            name=protocol.public_name,
            description=protocol.public_description or "",
            supported_interfaces=[interface],
            version="1.0",
            capabilities=capabilities,
            default_input_modes=["text", "data"],
            default_output_modes=list(protocol.output_modes),
            skills=[
                a2a.AgentSkill(
                    id=skill.id,
                    name=skill.name,
                    description=skill.description or "",
                    input_modes=["text", "data"],
                    output_modes=list(protocol.output_modes),
                )
                for skill in protocol.a2a_skills
            ],
        )
        card.security_schemes["bearerAuth"].http_auth_security_scheme.CopyFrom(
            a2a.HTTPAuthSecurityScheme(
                description="Foundation Workspace-bound bearer API key",
                scheme="bearer",
                bearer_format="API key",
            )
        )
        card.security_requirements.add().schemes["bearerAuth"].list.append("")
        return card

    async def extended_agent_card(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        base_url: str,
    ) -> a2a.AgentCard:
        async with short_session(self._sessions) as database:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_read,
                )
            except AuthorizationError as error:
                raise _not_found() from error
        card = await self.public_agent_card(agent_id=agent_id, base_url=base_url)
        if not card.capabilities.extended_agent_card:
            raise A2AError(
                "extended_agent_card_not_configured",
                "The authenticated extended Agent Card is not configured.",
                status_code=400,
            )
        return card

    async def send(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        request: a2a.SendMessageRequest,
    ) -> a2a.Task:
        _validate_send_request(request)
        request_json = MessageToDict(request, preserving_proto_field_name=False)
        request_digest = canonical_digest(request_json)
        replay = await self._message_replay(
            actor=actor,
            agent_id=agent_id,
            message_id=request.message.message_id,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay

        submitted = _agent_input(request.message)
        context = await self._load_context(
            actor=actor,
            agent_id=agent_id,
            context_id=request.message.context_id or None,
        )
        if context is None:
            if request.message.task_id:
                raise A2AError("task_not_found", "The requested Task was not found.", status_code=404)
            return await self._start_task(
                actor=actor,
                agent_id=agent_id,
                submitted=submitted,
                request=request,
                request_json=request_json,
                request_digest=request_digest,
            )
        if request.message.task_id:
            return await self._continue_waiting_task(
                actor=actor,
                context=context,
                task_id=request.message.task_id,
                submitted=submitted,
                request=request,
                request_json=request_json,
                request_digest=request_digest,
            )
        return await self._start_context_task(
            actor=actor,
            context=context,
            submitted=submitted,
            request=request,
            request_json=request_json,
            request_digest=request_digest,
        )

    async def get_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
    ) -> a2a.Task:
        task, run = await self._load_task(actor=actor, agent_id=agent_id, task_id=task_id)
        return await self._project_task(task, run)

    async def list_tasks(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        context_id: str | None,
        limit: int,
    ) -> tuple[a2a.Task, ...]:
        async with short_session(self._sessions) as database:
            statement = (
                select(A2ATaskBindingRecord, RunRecord)
                .join(
                    A2AContextBindingRecord,
                    A2AContextBindingRecord.id == A2ATaskBindingRecord.context_binding_id,
                )
                .join(
                    RunRecord,
                    and_(
                        RunRecord.tenant_id == A2ATaskBindingRecord.organization_id,
                        RunRecord.id == A2ATaskBindingRecord.current_run_id,
                    ),
                )
                .where(
                    A2ATaskBindingRecord.workspace_id == actor.boundary_workspace_id,
                    A2ATaskBindingRecord.agent_id == agent_id,
                    A2AContextBindingRecord.client_principal_type == actor.principal.principal_type.value,
                    A2AContextBindingRecord.client_principal_id == actor.principal.principal_id,
                )
                .order_by(A2ATaskBindingRecord.created_at.desc(), A2ATaskBindingRecord.id.desc())
                .limit(limit)
            )
            if context_id is not None:
                statement = statement.where(A2ATaskBindingRecord.context_id == context_id)
            rows = tuple((await database.execute(statement)).all())
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=agent_id,
                    action=WorkspaceAction.run_read,
                )
            except AuthorizationError as error:
                raise _not_found() from error
        return tuple([await self._project_task(task, run) for task, run in rows])

    async def cancel_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
    ) -> a2a.Task:
        task, run, thread = await self._load_task_with_thread(actor=actor, agent_id=agent_id, task_id=task_id)
        if run.status in _TERMINAL:
            return await self._project_task(task, run)
        await self._commands.interrupt(
            actor=actor,
            run_id=run.id,
            idempotency_key=f"a2a-cancel:{task.id}:{run.id}",
            request=InterruptRequest(expected_run_version=run.version, expected_thread_version=thread.version),
        )
        return await self.get_task(actor=actor, agent_id=agent_id, task_id=task_id)

    async def stream_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
    ) -> AsyncIterator[a2a.StreamResponse]:
        initial = await self.get_task(actor=actor, agent_id=agent_id, task_id=task_id)
        yield a2a.StreamResponse(task=initial)
        initial_state = initial.status.state
        if initial_state in {
            a2a.TASK_STATE_COMPLETED,
            a2a.TASK_STATE_FAILED,
            a2a.TASK_STATE_CANCELED,
            a2a.TASK_STATE_INPUT_REQUIRED,
            a2a.TASK_STATE_AUTH_REQUIRED,
        }:
            return
        started = monotonic()
        last_state = initial_state
        while monotonic() - started < self._maximum_wait_seconds:
            await anyio.sleep(self._poll_interval_seconds)
            current = await self.get_task(actor=actor, agent_id=agent_id, task_id=task_id)
            if current.status.state == last_state:
                continue
            last_state = current.status.state
            update = a2a.TaskStatusUpdateEvent(
                task_id=current.id,
                context_id=current.context_id,
                status=current.status,
            )
            yield a2a.StreamResponse(status_update=update)
            if last_state in {
                a2a.TASK_STATE_COMPLETED,
                a2a.TASK_STATE_FAILED,
                a2a.TASK_STATE_CANCELED,
                a2a.TASK_STATE_INPUT_REQUIRED,
                a2a.TASK_STATE_AUTH_REQUIRED,
            }:
                return

    async def wait_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
    ) -> a2a.Task:
        started = monotonic()
        while True:
            task = await self.get_task(actor=actor, agent_id=agent_id, task_id=task_id)
            if (
                task.status.state
                in {
                    a2a.TASK_STATE_COMPLETED,
                    a2a.TASK_STATE_FAILED,
                    a2a.TASK_STATE_CANCELED,
                    a2a.TASK_STATE_INPUT_REQUIRED,
                    a2a.TASK_STATE_AUTH_REQUIRED,
                }
                or monotonic() - started >= self._maximum_wait_seconds
            ):
                return task
            await anyio.sleep(self._poll_interval_seconds)

    async def _start_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        submitted: AgentInput,
        request: a2a.SendMessageRequest,
        request_json: dict[str, Any],
        request_digest: str,
    ) -> a2a.Task:
        context_binding_id = new_object_id("a2actx")
        context_id = request.message.context_id or context_binding_id
        task_id = new_object_id("a2atask")
        message_binding_id = new_object_id("a2amsg")
        now = assume_utc(self._clock())

        async def bind(database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
            run = await database.get(RunRecord, receipt.run_id)
            if run is None:
                raise RuntimeError("accepted A2A Run is missing")
            database.add(
                A2AContextBindingRecord(
                    id=context_binding_id,
                    organization_id=run.tenant_id,
                    workspace_id=actor.boundary_workspace_id,
                    client_principal_type=actor.principal.principal_type.value,
                    client_principal_id=actor.principal.principal_id,
                    agent_id=agent_id,
                    context_id=context_id,
                    session_id=receipt.session_id,
                    root_thread_id=receipt.thread_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await database.flush()
            database.add(
                A2ATaskBindingRecord(
                    id=task_id,
                    organization_id=run.tenant_id,
                    workspace_id=actor.boundary_workspace_id,
                    context_binding_id=context_binding_id,
                    context_id=context_id,
                    agent_id=agent_id,
                    agent_revision_id=run.agent_revision_id,
                    thread_id=receipt.thread_id,
                    current_run_id=receipt.run_id,
                    run_ids_json=[receipt.run_id],
                    client_tool_surface_digest=_EMPTY_DIGEST,
                    created_at=now,
                    updated_at=now,
                )
            )
            await database.flush()
            database.add(
                _message_record(
                    binding_id=message_binding_id,
                    actor=actor,
                    agent_id=agent_id,
                    task_id=task_id,
                    request=request,
                    request_json=request_json,
                    request_digest=request_digest,
                    run=run,
                    now=now,
                )
            )

        await self._commands.start(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            idempotency_key=f"a2a:{agent_id}:{request.message.message_id}",
            request=StartRunRequest(agent_id=agent_id, input=submitted),
            transaction_hook=bind,
        )
        return await self.get_task(actor=actor, agent_id=agent_id, task_id=task_id)

    async def _start_context_task(
        self,
        *,
        actor: AuthenticatedActor,
        context: A2AContextBindingRecord,
        submitted: AgentInput,
        request: a2a.SendMessageRequest,
        request_json: dict[str, Any],
        request_digest: str,
    ) -> a2a.Task:
        previous, current, thread = await self._latest_context_task(context)
        if previous is not None and current.status not in _TERMINAL:
            raise A2AError("task_not_terminal", "The Context already has active Task work.", status_code=409)
        task_id = new_object_id("a2atask")
        now = assume_utc(self._clock())

        async def bind(database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
            run = await database.get(RunRecord, receipt.run_id)
            if run is None:
                raise RuntimeError("accepted A2A continuation Run is missing")
            database.add(
                A2ATaskBindingRecord(
                    id=task_id,
                    organization_id=run.tenant_id,
                    workspace_id=context.workspace_id,
                    context_binding_id=context.id,
                    context_id=context.context_id,
                    agent_id=context.agent_id,
                    agent_revision_id=run.agent_revision_id,
                    thread_id=receipt.thread_id,
                    current_run_id=receipt.run_id,
                    run_ids_json=[receipt.run_id],
                    client_tool_surface_digest=_EMPTY_DIGEST,
                    created_at=now,
                    updated_at=now,
                )
            )
            await database.flush()
            database.add(
                _message_record(
                    binding_id=new_object_id("a2amsg"),
                    actor=actor,
                    agent_id=context.agent_id,
                    task_id=task_id,
                    request=request,
                    request_json=request_json,
                    request_digest=request_digest,
                    run=run,
                    now=now,
                )
            )

        continuation = ContinueRunRequest(expected_thread_version=thread.version, input=submitted)
        if thread.head_run_id is None:
            receipt = await self._commands.continue_empty_thread(
                actor=actor,
                thread_id=thread.id,
                idempotency_key=f"a2a:{context.agent_id}:{request.message.message_id}",
                request=continuation,
                transaction_hook=bind,
            )
        else:
            receipt = await self._commands.continue_from(
                actor=actor,
                source_run_id=thread.head_run_id,
                idempotency_key=f"a2a:{context.agent_id}:{request.message.message_id}",
                request=continuation,
                transaction_hook=bind,
            )
        del receipt
        return await self.get_task(actor=actor, agent_id=context.agent_id, task_id=task_id)

    async def _continue_waiting_task(
        self,
        *,
        actor: AuthenticatedActor,
        context: A2AContextBindingRecord,
        task_id: str,
        submitted: AgentInput,
        request: a2a.SendMessageRequest,
        request_json: dict[str, Any],
        request_digest: str,
    ) -> a2a.Task:
        task, run, thread = await self._load_task_with_thread(
            actor=actor,
            agent_id=context.agent_id,
            task_id=task_id,
        )
        sealed_state_digest = run.sealed_state_digest_sha256
        if (
            task.context_binding_id != context.id
            or run.status != RunStatus.waiting.value
            or sealed_state_digest is None
        ):
            raise A2AError("task_not_continuable", "The Task is not waiting for input.", status_code=409)
        now = assume_utc(self._clock())

        async def bind(database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
            selected = (
                await database.execute(
                    select(A2ATaskBindingRecord).where(A2ATaskBindingRecord.id == task.id).with_for_update()
                )
            ).scalar_one()
            accepted = await database.get(RunRecord, receipt.run_id)
            if accepted is None:
                raise RuntimeError("accepted A2A feedback Run is missing")
            selected.current_run_id = receipt.run_id
            selected.run_ids_json = [*selected.run_ids_json, receipt.run_id]
            selected.updated_at = now
            database.add(
                _message_record(
                    binding_id=new_object_id("a2amsg"),
                    actor=actor,
                    agent_id=context.agent_id,
                    task_id=task.id,
                    request=request,
                    request_json=request_json,
                    request_digest=request_digest,
                    run=accepted,
                    now=now,
                )
            )

        await self._commands.continue_waiting(
            actor=actor,
            run_id=run.id,
            idempotency_key=f"a2a:{context.agent_id}:{request.message.message_id}",
            request=WaitingContinueRunRequest(
                expected_thread_version=thread.version,
                sealed_state_digest_sha256=sealed_state_digest,
                input=submitted,
            ),
            transaction_hook=bind,
        )
        return await self.get_task(actor=actor, agent_id=context.agent_id, task_id=task.id)

    async def _message_replay(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        message_id: str,
        request_digest: str,
    ) -> a2a.Task | None:
        async with short_session(self._sessions) as database:
            binding = await database.scalar(
                select(A2AMessageBindingRecord).where(
                    A2AMessageBindingRecord.workspace_id == actor.boundary_workspace_id,
                    A2AMessageBindingRecord.client_principal_type == actor.principal.principal_type.value,
                    A2AMessageBindingRecord.client_principal_id == actor.principal.principal_id,
                    A2AMessageBindingRecord.agent_id == agent_id,
                    A2AMessageBindingRecord.message_id == message_id,
                )
            )
        if binding is None:
            return None
        if binding.request_digest_sha256 != request_digest:
            raise A2AError("message_id_conflict", "The Message ID was reused with different content.", status_code=409)
        return await self.get_task(actor=actor, agent_id=agent_id, task_id=binding.task_id)

    async def _load_context(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        context_id: str | None,
    ) -> A2AContextBindingRecord | None:
        if context_id is None:
            return None
        async with short_session(self._sessions) as database:
            return await database.scalar(
                select(A2AContextBindingRecord).where(
                    A2AContextBindingRecord.workspace_id == actor.boundary_workspace_id,
                    A2AContextBindingRecord.client_principal_type == actor.principal.principal_type.value,
                    A2AContextBindingRecord.client_principal_id == actor.principal.principal_id,
                    A2AContextBindingRecord.agent_id == agent_id,
                    A2AContextBindingRecord.context_id == context_id,
                )
            )

    async def _latest_context_task(
        self,
        context: A2AContextBindingRecord,
    ) -> tuple[A2ATaskBindingRecord | None, RunRecord, ThreadRecord]:
        async with short_session(self._sessions) as database:
            task = await database.scalar(
                select(A2ATaskBindingRecord)
                .where(A2ATaskBindingRecord.context_binding_id == context.id)
                .order_by(A2ATaskBindingRecord.created_at.desc(), A2ATaskBindingRecord.id.desc())
                .limit(1)
            )
            thread = await database.scalar(
                select(ThreadRecord).where(
                    ThreadRecord.tenant_id == context.organization_id,
                    ThreadRecord.id == context.root_thread_id,
                )
            )
            if thread is None:
                raise _not_found()
            current = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == context.organization_id,
                    RunRecord.id == thread.current_run_id,
                )
            )
            if current is None:
                raise _not_found()
            return task, current, thread

    async def _load_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
    ) -> tuple[A2ATaskBindingRecord, RunRecord]:
        task, run, _thread = await self._load_task_with_thread(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
        )
        return task, run

    async def _load_task_with_thread(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
    ) -> tuple[A2ATaskBindingRecord, RunRecord, ThreadRecord]:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(A2ATaskBindingRecord, RunRecord, ThreadRecord)
                    .join(
                        A2AContextBindingRecord,
                        A2AContextBindingRecord.id == A2ATaskBindingRecord.context_binding_id,
                    )
                    .join(
                        RunRecord,
                        and_(
                            RunRecord.tenant_id == A2ATaskBindingRecord.organization_id,
                            RunRecord.id == A2ATaskBindingRecord.current_run_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.tenant_id == A2ATaskBindingRecord.organization_id,
                            ThreadRecord.id == A2ATaskBindingRecord.thread_id,
                        ),
                    )
                    .where(
                        A2ATaskBindingRecord.id == task_id,
                        A2ATaskBindingRecord.workspace_id == actor.boundary_workspace_id,
                        A2ATaskBindingRecord.agent_id == agent_id,
                        A2AContextBindingRecord.client_principal_type == actor.principal.principal_type.value,
                        A2AContextBindingRecord.client_principal_id == actor.principal.principal_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            task, run, thread = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=agent_id,
                    action=WorkspaceAction.run_read,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            return task, run, thread

    async def _project_task(self, task: A2ATaskBindingRecord, run: RunRecord) -> a2a.Task:
        history: list[a2a.Message] = []
        async with short_session(self._sessions) as database:
            messages = tuple(
                (
                    await database.scalars(
                        select(A2AMessageBindingRecord)
                        .where(A2AMessageBindingRecord.task_id == task.id)
                        .order_by(A2AMessageBindingRecord.created_at, A2AMessageBindingRecord.id)
                    )
                ).all()
            )
        for binding in messages:
            message = a2a.Message()
            message_data = binding.request_json.get("message")
            if isinstance(message_data, dict):
                from google.protobuf.json_format import ParseDict

                ParseDict(message_data, message)
                history.append(message)
        status = _task_status(run)
        artifacts = _task_artifacts(task, run)
        return a2a.Task(
            id=task.id,
            context_id=task.context_id,
            status=status,
            artifacts=artifacts,
            history=history,
        )


_EMPTY_DIGEST = hashlib.sha256(b"[]").hexdigest()


def _message_record(
    *,
    binding_id: str,
    actor: AuthenticatedActor,
    agent_id: str,
    task_id: str,
    request: a2a.SendMessageRequest,
    request_json: dict[str, Any],
    request_digest: str,
    run: RunRecord,
    now: datetime,
) -> A2AMessageBindingRecord:
    return A2AMessageBindingRecord(
        id=binding_id,
        organization_id=run.tenant_id,
        workspace_id=actor.boundary_workspace_id,
        client_principal_type=actor.principal.principal_type.value,
        client_principal_id=actor.principal.principal_id,
        agent_id=agent_id,
        task_id=task_id,
        message_id=request.message.message_id,
        request_digest_sha256=request_digest,
        request_json=request_json,
        run_id=run.id,
        created_at=now,
    )


def _validate_send_request(request: a2a.SendMessageRequest) -> None:
    if request.tenant:
        raise A2AError("tenant_not_supported", "A2A tenant selection is not supported.", status_code=400)
    message = request.message
    if not message.message_id or len(message.message_id.encode("utf-8")) > 512:
        raise A2AError("message_invalid", "A bounded messageId is required.", status_code=400)
    if message.role != a2a.ROLE_USER:
        raise A2AError("message_invalid", "Only user Messages can be submitted.", status_code=400)
    if message.extensions:
        raise A2AError("extension_not_supported", "A2A extensions are not supported.", status_code=400)
    if not message.parts:
        raise A2AError("message_invalid", "At least one Message Part is required.", status_code=400)


def _agent_input(message: a2a.Message) -> AgentInput:
    content: list[dict[str, str]] = []
    data: list[Any] = []
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            if not part.text:
                raise A2AError("message_invalid", "Text Parts cannot be empty.", status_code=400)
            content.append({"type": "text", "text": part.text})
        elif kind == "data":
            data.append(MessageToDict(part)["data"])
        elif kind in {"raw", "url"}:
            raise A2AError(
                "part_import_unavailable",
                "Raw and URL Part import is not available for this request.",
                status_code=422,
            )
        else:
            raise A2AError("message_invalid", "The Message Part content is missing.", status_code=400)
    structured: Any | None = None if not data else data[0] if len(data) == 1 else data
    return AgentInput.model_validate(
        {
            "schema_version": "2",
            "content": content,
            "structured_content": structured,
        }
    )


def _task_status(run: RunRecord) -> a2a.TaskStatus:
    state = {
        RunStatus.accepted.value: a2a.TASK_STATE_SUBMITTED,
        RunStatus.running.value: a2a.TASK_STATE_WORKING,
        RunStatus.waiting.value: a2a.TASK_STATE_AUTH_REQUIRED
        if run.wait_reason == "authentication"
        else a2a.TASK_STATE_INPUT_REQUIRED,
        RunStatus.completed.value: a2a.TASK_STATE_COMPLETED,
        RunStatus.failed.value: a2a.TASK_STATE_FAILED,
        RunStatus.cancelled.value: a2a.TASK_STATE_CANCELED,
    }[run.status]
    status = a2a.TaskStatus(state=state)
    timestamp = run.updated_at
    status.timestamp.FromDatetime(assume_utc(timestamp))
    if run.status == RunStatus.waiting.value and run.pending_json is not None:
        value = Value()
        from google.protobuf.json_format import ParseDict

        ParseDict(run.pending_json, value)
        status.message.CopyFrom(
            a2a.Message(
                message_id=f"status-{run.id}",
                task_id="",
                role=a2a.ROLE_AGENT,
                parts=[a2a.Part(data=value)],
            )
        )
    elif run.status == RunStatus.failed.value:
        message = "The Agent task failed."
        if isinstance(run.failure_json, dict) and isinstance(run.failure_json.get("message"), str):
            message = run.failure_json["message"]
        status.message.CopyFrom(
            a2a.Message(message_id=f"status-{run.id}", role=a2a.ROLE_AGENT, parts=[a2a.Part(text=message)])
        )
    return status


def _task_artifacts(task: A2ATaskBindingRecord, run: RunRecord) -> list[a2a.Artifact]:
    if run.status != RunStatus.completed.value:
        return []
    output = run.output_json
    if run.output_text is not None:
        parts = [a2a.Part(text=run.output_text)]
    elif isinstance(output, str):
        parts = [a2a.Part(text=output)]
    elif output is not None:
        value = Value()
        from google.protobuf.json_format import ParseDict

        ParseDict(output, value)
        parts = [a2a.Part(data=value)]
    else:
        return []
    return [
        a2a.Artifact(
            artifact_id=f"artifact-{task.id}-result",
            name="result",
            parts=parts,
        )
    ]


def _not_found() -> A2AError:
    return A2AError("resource_not_found", "The requested A2A resource was not found.", status_code=404)


__all__ = ["A2AError", "A2AService"]
