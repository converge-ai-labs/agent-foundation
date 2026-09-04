"""A2A 1.0 adapter over the shared Foundation interaction commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

import anyio
from a2a.types import a2a_pb2 as a2a
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Value
from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentConfig, canonical_digest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.collection_cursors import (
    CollectionCursorMismatchError,
    InvalidCollectionCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.ids import new_object_id
from a13n_service.interactions import InterruptRequest, RunAcceptanceReceipt, RunStatus
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.public_errors import PublicError
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, require_aware_utc, utc_now

from .a2a_import import A2APartImporter, A2APartImportError, PreparedA2AMessage
from .commands import ContinueRunRequest, NativeInteractionCommands, StartRunRequest, WaitingContinueRunRequest
from .models import (
    A2AContextBindingRecord,
    A2AMessageBindingRecord,
    A2APushConfigurationRecord,
    A2ATaskBindingRecord,
)

_TERMINAL = frozenset({RunStatus.completed.value, RunStatus.failed.value, RunStatus.cancelled.value})
_MAX_PUSH_CONFIGURATIONS = 10
_PUSH_TOKEN_KEY = "token"
_PUSH_CREDENTIALS_KEY = "authentication_credentials"


@dataclass(frozen=True, slots=True)
class A2ATaskPage:
    tasks: tuple[a2a.Task, ...]
    next_page_token: str | None
    total_size: int


@dataclass(frozen=True, slots=True)
class _PreparedPushConfiguration:
    id: str
    endpoint_url: str
    authentication_scheme: str | None
    token: str | None
    credentials: str | None


class A2AError(PublicError):
    """A bounded A2A adapter failure safe for the public binding."""


class A2AService:
    """Persist A2A identities and project their work through Native commands."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        commands: NativeInteractionCommands,
        secret_protector: SecretProtector,
        endpoint_policy: EndpointPolicy,
        part_importer: A2APartImporter,
        *,
        poll_interval_seconds: float,
        maximum_wait_seconds: float,
        push_drain_timeout_seconds: float,
        clock: Clock = utc_now,
    ) -> None:
        if push_drain_timeout_seconds <= 0:
            raise ValueError("A2A push drain timeout must be positive")
        self._sessions = sessions
        self._commands = commands
        self._secret_protector = secret_protector
        self._endpoint_policy = endpoint_policy
        self._part_importer = part_importer
        self._poll_interval_seconds = poll_interval_seconds
        self._maximum_wait_seconds = maximum_wait_seconds
        self._push_drain_timeout_seconds = push_drain_timeout_seconds
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
        history_length = _send_history_length(request.configuration)
        request_json = MessageToDict(request, preserving_proto_field_name=False)
        request_digest = canonical_digest(request_json)
        replay = await self._message_replay(
            actor=actor,
            agent_id=agent_id,
            message_id=request.message.message_id,
            request_digest=request_digest,
        )
        if replay is not None:
            return _apply_history_length(replay, history_length)

        context = await self._load_context(
            actor=actor,
            agent_id=agent_id,
            context_id=request.message.context_id or None,
        )
        await self._validate_accepted_output_modes(
            actor=actor,
            agent_id=agent_id,
            task_id=request.message.task_id or None,
            context=context,
            accepted=tuple(request.configuration.accepted_output_modes),
        )
        push_configuration = await self._prepare_send_push_configuration(
            actor=actor,
            agent_id=agent_id,
            request=request,
        )
        try:
            prepared = await self._part_importer.prepare(actor=actor, message=request.message)
        except A2APartImportError as error:
            raise A2AError(error.code, str(error), status_code=error.status_code) from error
        try:
            if context is None:
                if request.message.task_id:
                    raise A2AError("task_not_found", "The requested Task was not found.", status_code=404)
                task = await self._start_task(
                    actor=actor,
                    agent_id=agent_id,
                    prepared=prepared,
                    request=request,
                    request_json=request_json,
                    request_digest=request_digest,
                    push_configuration=push_configuration,
                )
            elif request.message.task_id:
                task = await self._continue_waiting_task(
                    actor=actor,
                    context=context,
                    task_id=request.message.task_id,
                    prepared=prepared,
                    request=request,
                    request_json=request_json,
                    request_digest=request_digest,
                )
            else:
                task = await self._start_context_task(
                    actor=actor,
                    context=context,
                    prepared=prepared,
                    request=request,
                    request_json=request_json,
                    request_digest=request_digest,
                    push_configuration=push_configuration,
                )
            return _apply_history_length(task, history_length)
        finally:
            await self._part_importer.discard(prepared)

    async def _validate_accepted_output_modes(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str | None,
        context: A2AContextBindingRecord | None,
        accepted: tuple[str, ...],
    ) -> None:
        if not accepted:
            return
        async with short_session(self._sessions) as database:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_invoke,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            if task_id is None:
                agent = await database.scalar(
                    select(AgentRecord).where(
                        AgentRecord.id == agent_id,
                        AgentRecord.workspace_id == actor.boundary_workspace_id,
                        AgentRecord.enabled.is_(True),
                        AgentRecord.archived_at.is_(None),
                    )
                )
                if agent is None:
                    raise _not_found()
                revision_id = agent.current_revision_id
                organization_id = agent.organization_id
            else:
                if context is None:
                    raise _not_found()
                task = await database.scalar(
                    select(A2ATaskBindingRecord).where(
                        A2ATaskBindingRecord.id == task_id,
                        A2ATaskBindingRecord.context_binding_id == context.id,
                        A2ATaskBindingRecord.agent_id == agent_id,
                    )
                )
                if task is None:
                    raise _not_found()
                revision_id = task.agent_revision_id
                organization_id = task.organization_id
            revision = await database.scalar(
                select(AgentRevisionRecord).where(
                    AgentRevisionRecord.id == revision_id,
                    AgentRevisionRecord.agent_id == agent_id,
                    AgentRevisionRecord.organization_id == organization_id,
                    AgentRevisionRecord.workspace_id == actor.boundary_workspace_id,
                )
            )
            if revision is None:
                raise _not_found()
        configured = frozenset(AgentConfig.model_validate(revision.config).protocol.output_modes)
        if configured.isdisjoint(accepted):
            raise A2AError(
                "output_mode_not_supported",
                "The Agent cannot produce any accepted output mode.",
                status_code=400,
            )

    async def get_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
        history_length: int | None = None,
    ) -> a2a.Task:
        task, run = await self._load_task(actor=actor, agent_id=agent_id, task_id=task_id)
        return await self._project_task(task, run, history_length=history_length)

    async def list_tasks(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        context_id: str | None,
        status: a2a.TaskState | None,
        page_size: int,
        page_token: str | None,
        history_length: int | None,
        status_timestamp_after: datetime | None,
        include_artifacts: bool,
    ) -> A2ATaskPage:
        if page_size < 1 or page_size > 100:
            raise A2AError("invalid_page_size", "pageSize must be between 1 and 100.", status_code=400)
        if history_length is not None and history_length < 0:
            raise A2AError("invalid_history_length", "historyLength must not be negative.", status_code=400)
        status_filter = _task_state_filter(status)
        try:
            after_timestamp = None if status_timestamp_after is None else require_aware_utc(status_timestamp_after)
        except ValueError as error:
            raise A2AError(
                "invalid_status_timestamp", "statusTimestampAfter must include a timezone offset.", status_code=400
            ) from error
        scope = _task_cursor_scope(
            actor=actor,
            agent_id=agent_id,
            context_id=context_id,
            status=status,
            history_length=history_length,
            status_timestamp_after=after_timestamp,
            include_artifacts=include_artifacts,
        )
        after: tuple[datetime, str] | None = None
        if page_token:
            try:
                payload = decode_collection_cursor(page_token, scope=scope, kind="a2a_tasks")
                updated_at = payload.get("updated_at")
                task_id = payload.get("task_id")
                if not isinstance(updated_at, str) or not isinstance(task_id, str):
                    raise InvalidCollectionCursorError
                after = (require_aware_utc(datetime.fromisoformat(updated_at)), task_id)
            except (CollectionCursorMismatchError, InvalidCollectionCursorError, ValueError) as error:
                raise A2AError("invalid_page_token", "The page token is invalid.", status_code=400) from error
        async with short_session(self._sessions) as database:
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
            base = (
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
            )
            if context_id is not None:
                base = base.where(A2ATaskBindingRecord.context_id == context_id)
            if status_filter is not None:
                base = base.where(status_filter)
            if after_timestamp is not None:
                base = base.where(RunRecord.updated_at >= after_timestamp)
            total_size = int((await database.scalar(select(func.count()).select_from(base.subquery()))) or 0)
            if after is not None:
                updated_at, task_id = after
                base = base.where(
                    or_(
                        RunRecord.updated_at < updated_at,
                        and_(RunRecord.updated_at == updated_at, A2ATaskBindingRecord.id < task_id),
                    )
                )
            rows = tuple(
                (
                    await database.execute(
                        base.order_by(RunRecord.updated_at.desc(), A2ATaskBindingRecord.id.desc()).limit(page_size + 1)
                    )
                ).all()
            )
        page = rows[:page_size]
        next_page_token = None
        if len(rows) > page_size:
            last_task, last_run = page[-1]
            next_page_token = encode_collection_cursor(
                {"updated_at": assume_utc(last_run.updated_at).isoformat(), "task_id": last_task.id},
                scope=scope,
                kind="a2a_tasks",
            )
        tasks = tuple(
            [
                await self._project_task(
                    task,
                    run,
                    history_length=history_length,
                    include_artifacts=include_artifacts,
                )
                for task, run in page
            ]
        )
        return A2ATaskPage(tasks=tasks, next_page_token=next_page_token, total_size=total_size)

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

    async def _prepare_send_push_configuration(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        request: a2a.SendMessageRequest,
    ) -> _PreparedPushConfiguration | None:
        configuration = request.configuration
        if not configuration.HasField("task_push_notification_config"):
            return None
        if request.message.task_id:
            raise A2AError(
                "push_configuration_not_allowed",
                "A Send Message push configuration can only be registered with a new Task.",
                status_code=400,
            )
        requested = configuration.task_push_notification_config
        _validate_push_configuration(requested, task_id="")
        async with short_session(self._sessions) as database:
            await self._authorize_agent_in_transaction(
                database,
                actor=actor,
                agent_id=agent_id,
                action=WorkspaceAction.a2a_push_configuration_manage,
            )
        try:
            endpoint_url = await self._endpoint_policy.validate(requested.url)
        except EndpointPolicyError as error:
            raise A2AError("invalid_push_destination", str(error), status_code=400) from error
        return _PreparedPushConfiguration(
            id=new_object_id("a2apush"),
            endpoint_url=endpoint_url,
            authentication_scheme=requested.authentication.scheme or None,
            token=requested.token or None,
            credentials=requested.authentication.credentials or None,
        )

    async def create_push_configuration(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
        requested: a2a.TaskPushNotificationConfig,
    ) -> a2a.TaskPushNotificationConfig:
        """Create one future-only A2A push destination with write-only secrets."""

        _validate_push_configuration(requested, task_id=task_id)
        await self._authorize_task_action(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            action=WorkspaceAction.a2a_push_configuration_manage,
        )
        try:
            endpoint_url = await self._endpoint_policy.validate(requested.url)
        except EndpointPolicyError as error:
            raise A2AError("invalid_push_destination", str(error), status_code=400) from error
        prepared = _PreparedPushConfiguration(
            id=new_object_id("a2apush"),
            endpoint_url=endpoint_url,
            authentication_scheme=requested.authentication.scheme or None,
            token=requested.token or None,
            credentials=requested.authentication.credentials or None,
        )
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            task = await database.scalar(
                select(A2ATaskBindingRecord)
                .where(
                    A2ATaskBindingRecord.id == task_id,
                    A2ATaskBindingRecord.workspace_id == actor.boundary_workspace_id,
                    A2ATaskBindingRecord.agent_id == agent_id,
                )
                .with_for_update()
            )
            if task is None:
                raise _not_found()
            record = await self._commit_push_configuration(
                database,
                actor=actor,
                task=task,
                prepared=prepared,
                now=now,
            )
        return _project_push_configuration(record)

    async def _commit_push_configuration(
        self,
        database: AsyncSession,
        *,
        actor: AuthenticatedActor,
        task: A2ATaskBindingRecord,
        prepared: _PreparedPushConfiguration,
        now: datetime,
    ) -> A2APushConfigurationRecord:
        await self._authorize_agent_in_transaction(
            database,
            actor=actor,
            agent_id=task.agent_id,
            action=WorkspaceAction.a2a_push_configuration_manage,
        )
        rows = (
            await database.scalars(
                select(A2APushConfigurationRecord.id)
                .where(
                    A2APushConfigurationRecord.organization_id == task.organization_id,
                    A2APushConfigurationRecord.task_id == task.id,
                    A2APushConfigurationRecord.state == "active",
                )
                .limit(_MAX_PUSH_CONFIGURATIONS)
            )
        ).all()
        if len(rows) >= _MAX_PUSH_CONFIGURATIONS:
            raise A2AError(
                "push_configuration_limit",
                "The Task has reached its push configuration limit.",
                status_code=409,
            )
        record = A2APushConfigurationRecord(
            id=prepared.id,
            organization_id=task.organization_id,
            workspace_id=task.workspace_id,
            task_id=task.id,
            creator_principal_type=actor.principal.principal_type.value,
            creator_principal_id=actor.principal.principal_id,
            endpoint_url=prepared.endpoint_url,
            authentication_scheme=prepared.authentication_scheme,
            credential_generation=0,
            state="active",
            protocol_version="1.0",
            delivery_generation=1,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        try:
            record.replace_credential(
                _credential_bundle(prepared.token, prepared.credentials),
                self._secret_protector,
            )
        except SecretProtectionError as error:
            raise A2AError(
                "push_credential_invalid",
                "The push credential bundle could not be protected.",
                status_code=400,
            ) from error
        database.add(record)
        await database.flush()
        return record

    async def get_push_configuration(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
        config_id: str,
    ) -> a2a.TaskPushNotificationConfig:
        await self._authorize_task_action(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            action=WorkspaceAction.a2a_push_configuration_read,
        )
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(A2APushConfigurationRecord).where(
                    A2APushConfigurationRecord.id == config_id,
                    A2APushConfigurationRecord.workspace_id == actor.boundary_workspace_id,
                    A2APushConfigurationRecord.task_id == task_id,
                    A2APushConfigurationRecord.state == "active",
                )
            )
        if record is None:
            raise _not_found()
        return _project_push_configuration(record)

    async def list_push_configurations(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
        page_size: int,
        page_token: str | None,
    ) -> tuple[tuple[a2a.TaskPushNotificationConfig, ...], str | None]:
        task, _run = await self._authorize_task_action(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            action=WorkspaceAction.a2a_push_configuration_read,
        )
        scope = _push_cursor_scope(actor=actor, agent_id=agent_id, task_id=task_id)
        after: tuple[datetime, str] | None = None
        if page_token:
            try:
                payload = decode_collection_cursor(page_token, scope=scope, kind="a2a_push")
                created_at = payload.get("created_at")
                config_id = payload.get("config_id")
                if not isinstance(created_at, str) or not isinstance(config_id, str):
                    raise InvalidCollectionCursorError
                after = (datetime.fromisoformat(created_at), config_id)
            except (CollectionCursorMismatchError, InvalidCollectionCursorError, ValueError) as error:
                raise A2AError("invalid_page_token", "The page token is invalid.", status_code=400) from error
        statement = select(A2APushConfigurationRecord).where(
            A2APushConfigurationRecord.organization_id == task.organization_id,
            A2APushConfigurationRecord.workspace_id == task.workspace_id,
            A2APushConfigurationRecord.task_id == task.id,
            A2APushConfigurationRecord.state == "active",
        )
        if after is not None:
            created_at, config_id = after
            statement = statement.where(
                or_(
                    A2APushConfigurationRecord.created_at < created_at,
                    and_(
                        A2APushConfigurationRecord.created_at == created_at,
                        A2APushConfigurationRecord.id < config_id,
                    ),
                )
            )
        async with short_session(self._sessions) as database:
            page = tuple(
                (
                    await database.scalars(
                        statement.order_by(
                            A2APushConfigurationRecord.created_at.desc(),
                            A2APushConfigurationRecord.id.desc(),
                        ).limit(page_size + 1)
                    )
                ).all()
            )
        records = page[:page_size]
        next_page_token = None
        if len(page) > page_size:
            last = records[-1]
            next_page_token = encode_collection_cursor(
                {"created_at": last.created_at.isoformat(), "config_id": last.id},
                scope=scope,
                kind="a2a_push",
            )
        return tuple(_project_push_configuration(record) for record in records), next_page_token

    async def delete_push_configuration(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
        config_id: str,
    ) -> None:
        await self._authorize_task_action(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            action=WorkspaceAction.a2a_push_configuration_manage,
        )
        now = assume_utc(self._clock())
        destination_ref: str
        async with transaction(self._sessions) as database:
            record = await database.scalar(
                select(A2APushConfigurationRecord)
                .where(
                    A2APushConfigurationRecord.id == config_id,
                    A2APushConfigurationRecord.workspace_id == actor.boundary_workspace_id,
                    A2APushConfigurationRecord.task_id == task_id,
                )
                .with_for_update()
            )
            if record is None:
                raise _not_found()
            if record.state == "active":
                await self._authorize_agent_in_transaction(
                    database,
                    actor=actor,
                    agent_id=agent_id,
                    action=WorkspaceAction.a2a_push_configuration_manage,
                )
                generation = record.delivery_generation
                record.replace_credential(None, self._secret_protector)
                record.state = "disabled"
                record.delivery_generation += 1
                record.updated_at = now
                record.deleted_at = now
            else:
                generation = record.delivery_generation - 1
            destination_ref = f"{record.id}:{generation}"

        await self._drain_push_destination(destination_ref)
        async with transaction(self._sessions) as database:
            await database.execute(
                delete(OutboxRecord).where(
                    OutboxRecord.destination_kind == "a2a_push",
                    OutboxRecord.destination_ref == destination_ref,
                )
            )

    async def _drain_push_destination(self, destination_ref: str) -> None:
        started = monotonic()
        while True:
            async with short_session(self._sessions) as database:
                publishing = bool(
                    await database.scalar(
                        select(
                            exists().where(
                                OutboxRecord.destination_kind == "a2a_push",
                                OutboxRecord.destination_ref == destination_ref,
                                OutboxRecord.status == "publishing",
                            )
                        )
                    )
                )
            if not publishing:
                return
            if monotonic() - started >= self._push_drain_timeout_seconds:
                raise A2AError(
                    "push_configuration_drain_timeout",
                    "The push configuration is disabled but an earlier delivery is still draining.",
                    status_code=503,
                )
            await anyio.sleep(self._poll_interval_seconds)

    async def _authorize_task_action(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
        action: WorkspaceAction,
    ) -> tuple[A2ATaskBindingRecord, RunRecord]:
        task, run = await self._load_task(actor=actor, agent_id=agent_id, task_id=task_id)
        async with short_session(self._sessions) as database:
            await self._authorize_agent_in_transaction(
                database,
                actor=actor,
                agent_id=agent_id,
                action=action,
            )
        return task, run

    @staticmethod
    async def _authorize_agent_in_transaction(
        database: AsyncSession,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        action: WorkspaceAction,
    ) -> None:
        try:
            await authorize_agent(
                database,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                agent_id=agent_id,
                action=action,
            )
        except AuthorizationError as error:
            raise _not_found() from error

    async def stream_task(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        task_id: str,
        history_length: int | None = None,
    ) -> AsyncIterator[a2a.StreamResponse]:
        initial = await self.get_task(
            actor=actor,
            agent_id=agent_id,
            task_id=task_id,
            history_length=history_length,
        )
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
        delivered_artifacts = {_artifact_digest(artifact) for artifact in initial.artifacts}
        while monotonic() - started < self._maximum_wait_seconds:
            await anyio.sleep(self._poll_interval_seconds)
            current = await self.get_task(
                actor=actor,
                agent_id=agent_id,
                task_id=task_id,
                history_length=history_length,
            )
            for artifact in current.artifacts:
                digest = _artifact_digest(artifact)
                if digest in delivered_artifacts:
                    continue
                delivered_artifacts.add(digest)
                yield a2a.StreamResponse(
                    artifact_update=a2a.TaskArtifactUpdateEvent(
                        task_id=current.id,
                        context_id=current.context_id,
                        artifact=artifact,
                        append=False,
                        last_chunk=True,
                    )
                )
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
        history_length: int | None = None,
    ) -> a2a.Task:
        started = monotonic()
        while True:
            task = await self.get_task(
                actor=actor,
                agent_id=agent_id,
                task_id=task_id,
                history_length=history_length,
            )
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
        prepared: PreparedA2AMessage,
        request: a2a.SendMessageRequest,
        request_json: dict[str, Any],
        request_digest: str,
        push_configuration: _PreparedPushConfiguration | None,
    ) -> a2a.Task:
        context_binding_id = new_object_id("a2actx")
        context_id = request.message.context_id or context_binding_id
        task_id = new_object_id("a2atask")
        message_binding_id = new_object_id("a2amsg")
        now = assume_utc(self._clock())

        async def bind(database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
            await self._part_importer.commit_in_transaction(database, actor=actor, prepared=prepared)
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
            task = A2ATaskBindingRecord(
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
            database.add(task)
            await database.flush()
            if push_configuration is not None:
                await self._commit_push_configuration(
                    database,
                    actor=actor,
                    task=task,
                    prepared=push_configuration,
                    now=now,
                )
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
            request=StartRunRequest(agent_id=agent_id, input=prepared.input),
            transaction_hook=bind,
            prepared_assets=prepared.prepared_assets,
        )
        return await self.get_task(actor=actor, agent_id=agent_id, task_id=task_id)

    async def _start_context_task(
        self,
        *,
        actor: AuthenticatedActor,
        context: A2AContextBindingRecord,
        prepared: PreparedA2AMessage,
        request: a2a.SendMessageRequest,
        request_json: dict[str, Any],
        request_digest: str,
        push_configuration: _PreparedPushConfiguration | None,
    ) -> a2a.Task:
        previous, current, thread = await self._latest_context_task(context)
        if previous is not None and current.status not in _TERMINAL:
            raise A2AError("task_not_terminal", "The Context already has active Task work.", status_code=409)
        task_id = new_object_id("a2atask")
        now = assume_utc(self._clock())

        async def bind(database: AsyncSession, receipt: RunAcceptanceReceipt) -> None:
            await self._part_importer.commit_in_transaction(database, actor=actor, prepared=prepared)
            run = await database.get(RunRecord, receipt.run_id)
            if run is None:
                raise RuntimeError("accepted A2A continuation Run is missing")
            task = A2ATaskBindingRecord(
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
            database.add(task)
            await database.flush()
            if push_configuration is not None:
                await self._commit_push_configuration(
                    database,
                    actor=actor,
                    task=task,
                    prepared=push_configuration,
                    now=now,
                )
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

        continuation = ContinueRunRequest(expected_thread_version=thread.version, input=prepared.input)
        if thread.head_run_id is None:
            receipt = await self._commands.continue_empty_thread(
                actor=actor,
                thread_id=thread.id,
                idempotency_key=f"a2a:{context.agent_id}:{request.message.message_id}",
                request=continuation,
                transaction_hook=bind,
                prepared_assets=prepared.prepared_assets,
            )
        else:
            receipt = await self._commands.continue_from(
                actor=actor,
                source_run_id=thread.head_run_id,
                idempotency_key=f"a2a:{context.agent_id}:{request.message.message_id}",
                request=continuation,
                transaction_hook=bind,
                prepared_assets=prepared.prepared_assets,
            )
        del receipt
        return await self.get_task(actor=actor, agent_id=context.agent_id, task_id=task_id)

    async def _continue_waiting_task(
        self,
        *,
        actor: AuthenticatedActor,
        context: A2AContextBindingRecord,
        task_id: str,
        prepared: PreparedA2AMessage,
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
            await self._part_importer.commit_in_transaction(database, actor=actor, prepared=prepared)
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
                input=prepared.input,
            ),
            transaction_hook=bind,
            prepared_assets=prepared.prepared_assets,
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

    async def _project_task(
        self,
        task: A2ATaskBindingRecord,
        run: RunRecord,
        *,
        history_length: int | None = None,
        include_artifacts: bool = True,
    ) -> a2a.Task:
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
        selected_messages = messages if history_length is None else messages[-history_length:] if history_length else ()
        for binding in selected_messages:
            message = a2a.Message()
            message_data = binding.request_json.get("message")
            if isinstance(message_data, dict):
                from google.protobuf.json_format import ParseDict

                ParseDict(message_data, message)
                history.append(message)
        status = _task_status(run)
        artifacts = _task_artifacts(task, run) if include_artifacts else []
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
    _send_history_length(request.configuration)


def _send_history_length(configuration: a2a.SendMessageConfiguration) -> int | None:
    if not configuration.HasField("history_length"):
        return None
    if configuration.history_length < 0:
        raise A2AError("invalid_history_length", "historyLength must not be negative.", status_code=400)
    return configuration.history_length


def _apply_history_length(task: a2a.Task, history_length: int | None) -> a2a.Task:
    if history_length is None:
        return task
    projected = a2a.Task()
    projected.CopyFrom(task)
    history = tuple(projected.history)
    del projected.history[:]
    if history_length:
        projected.history.extend(history[-history_length:])
    return projected


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


def _validate_push_configuration(requested: a2a.TaskPushNotificationConfig, *, task_id: str) -> None:
    if requested.tenant:
        raise A2AError("tenant_not_supported", "A2A tenant selection is not supported.", status_code=400)
    if requested.id:
        raise A2AError("invalid_push_configuration", "The server assigns the push configuration ID.", status_code=400)
    if requested.task_id and requested.task_id != task_id:
        raise A2AError(
            "invalid_push_configuration", "The push configuration Task does not match the route.", status_code=400
        )
    if not requested.url or len(requested.url) > 2048:
        raise A2AError("invalid_push_configuration", "The push configuration URL is invalid.", status_code=400)
    if len(requested.token.encode("utf-8")) > 65_536:
        raise A2AError("invalid_push_configuration", "The push configuration token is too large.", status_code=400)
    authentication = requested.authentication
    if len(authentication.scheme) > 128 or len(authentication.credentials.encode("utf-8")) > 65_536:
        raise A2AError(
            "invalid_push_configuration",
            "The push authentication configuration is invalid.",
            status_code=400,
        )
    if authentication.credentials and not authentication.scheme:
        raise A2AError(
            "invalid_push_configuration",
            "Push authentication credentials require a scheme.",
            status_code=400,
        )
    _credential_bundle(requested.token or None, authentication.credentials or None)


def _credential_bundle(token: str | None, credentials: str | None) -> str | None:
    values = {
        key: value
        for key, value in ((_PUSH_TOKEN_KEY, token), (_PUSH_CREDENTIALS_KEY, credentials))
        if value is not None
    }
    if not values:
        return None
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 65_536:
        raise A2AError(
            "invalid_push_configuration",
            "The push credential bundle is too large.",
            status_code=400,
        )
    return encoded


def _project_push_configuration(record: A2APushConfigurationRecord) -> a2a.TaskPushNotificationConfig:
    projected = a2a.TaskPushNotificationConfig(
        id=record.id,
        task_id=record.task_id,
        url=record.endpoint_url,
    )
    if record.authentication_scheme is not None:
        projected.authentication.scheme = record.authentication_scheme
    return projected


def _task_state_filter(status: a2a.TaskState | None) -> Any | None:
    if status is None:
        return None
    filters = {
        a2a.TASK_STATE_SUBMITTED: RunRecord.status == RunStatus.accepted.value,
        a2a.TASK_STATE_WORKING: RunRecord.status == RunStatus.running.value,
        a2a.TASK_STATE_COMPLETED: RunRecord.status == RunStatus.completed.value,
        a2a.TASK_STATE_FAILED: RunRecord.status == RunStatus.failed.value,
        a2a.TASK_STATE_CANCELED: RunRecord.status == RunStatus.cancelled.value,
        a2a.TASK_STATE_INPUT_REQUIRED: and_(
            RunRecord.status == RunStatus.waiting.value,
            or_(RunRecord.wait_reason.is_(None), RunRecord.wait_reason != "authentication"),
        ),
        a2a.TASK_STATE_AUTH_REQUIRED: and_(
            RunRecord.status == RunStatus.waiting.value,
            RunRecord.wait_reason == "authentication",
        ),
        a2a.TASK_STATE_REJECTED: A2ATaskBindingRecord.id.is_(None),
    }
    selected = filters.get(status)
    if selected is None:
        raise A2AError("invalid_task_status", "The Task status filter is invalid.", status_code=400)
    return selected


def _task_cursor_scope(
    *,
    actor: AuthenticatedActor,
    agent_id: str,
    context_id: str | None,
    status: a2a.TaskState | None,
    history_length: int | None,
    status_timestamp_after: datetime | None,
    include_artifacts: bool,
) -> dict[str, object]:
    return {
        "workspace_id": actor.boundary_workspace_id,
        "principal_type": actor.principal.principal_type.value,
        "principal_id": actor.principal.principal_id,
        "agent_id": agent_id,
        "context_id": context_id,
        "status": status,
        "history_length": history_length,
        "status_timestamp_after": (None if status_timestamp_after is None else status_timestamp_after.isoformat()),
        "include_artifacts": include_artifacts,
    }


def _artifact_digest(artifact: a2a.Artifact) -> str:
    return canonical_digest(MessageToDict(artifact, preserving_proto_field_name=False))


def _push_cursor_scope(*, actor: AuthenticatedActor, agent_id: str, task_id: str) -> dict[str, object]:
    return {
        "workspace_id": actor.boundary_workspace_id,
        "principal_type": actor.principal.principal_type.value,
        "principal_id": actor.principal.principal_id,
        "agent_id": agent_id,
        "task_id": task_id,
    }


def _not_found() -> A2AError:
    return A2AError("resource_not_found", "The requested A2A resource was not found.", status_code=404)


__all__ = ["A2AError", "A2AService", "A2ATaskPage"]
