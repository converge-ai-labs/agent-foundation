"""Empty root Thread allocation without executing an Agent or provisioning a target."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.durable_operations.idempotency import is_evidence_unique_race
from a13n_service.durable_operations.requests import evidence_record, load_replay, request_identity
from a13n_service.environments.devices import ENVD_PROVIDER_KEYS, capture_device_target
from a13n_service.environments.domain import ExistingEnvironmentSelection, NewEnvironmentSelection
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.environments.selection import Omitted, allocate_selection, resolve_selection
from a13n_service.environments.websocket.admission import OnlineAdmission, OnlineEvidence
from a13n_service.iam import AuthenticatedActor, authorize_agent
from a13n_service.iam.authorization import WorkspaceAction, authorize_workspace
from a13n_service.ids import new_object_id
from a13n_service.labels import merge_labels
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from .domain import Thread, ThreadOriginKind, ThreadRole, new_thread_id
from .environment_selection import resolve_requested_environment
from .models import SessionRecord, ThreadRecord
from .records import thread_record
from .thread_domain import CreateThreadRequest


async def allocate_thread(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    body: CreateThreadRequest,
    idempotency_key: str,
    admission: OnlineAdmission | None = None,
) -> Thread:
    now = utc_now()
    normalized = body.model_dump(mode="json")
    if "environment" not in body.model_fields_set:
        normalized.pop("environment")
    identity = request_identity(idempotency_key, normalized)

    async def accept(session: AsyncSession, online: OnlineEvidence) -> Thread:
        workspace = await authorize_workspace(
            session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.agent_invoke
        )
        replay = await load_replay(
            session, actor=actor, operation="thread.create", scope_id=workspace_id, identity=identity, now=now
        )
        if replay:
            row = await session.get(ThreadRecord, replay.result_ref)
            if row is None:
                raise ApplicationError("thread_not_found", "Thread is unavailable", category=ErrorCategory.not_found)
            return replay.restore(Thread)
        selected = body.environment
        if body.agent_id is not None:
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=body.agent_id,
                action=WorkspaceAction.agent_invoke,
            )
            selected = await resolve_requested_environment(
                session,
                agent_id=body.agent_id,
                choice=body.environment if "environment" in body.model_fields_set else Omitted.UNSET,
            )
        environment_id = None
        working_directory = None
        if selected is not None:
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_template_use
                if isinstance(selected, NewEnvironmentSelection)
                else WorkspaceAction.environment_use,
            )
            resolved = await resolve_selection(session, workspace_id=workspace_id, choice=selected)
            environment = await allocate_selection(
                session,
                resolved,
                workspace_id=workspace_id,
                now=now,
                labels=selected.labels if isinstance(selected, NewEnvironmentSelection) else None,
            )
            environment_id = environment.id
            if isinstance(selected, ExistingEnvironmentSelection):
                working_directory = selected.working_directory
                provider = await session.get(EnvironmentProviderRecord, environment.provider_id)
                if provider is not None and provider.type in ENVD_PROVIDER_KEYS and working_directory is None:
                    target = await capture_device_target(session, environment, principal=actor.principal)
                    working_directory = online.working_directory(target)
        if body.session_id:
            if "session_labels" in body.model_fields_set:
                raise ApplicationError(
                    "creation_labels_not_allowed",
                    "Session labels can only be supplied when creating a Session.",
                    category=ErrorCategory.invalid_request,
                )
            parent = await session.scalar(
                select(SessionRecord)
                .where(
                    SessionRecord.id == body.session_id,
                    SessionRecord.workspace_id == workspace_id,
                    SessionRecord.organization_id == workspace.organization_id,
                )
                .with_for_update()
            )
            if parent is None:
                raise ApplicationError("session_not_found", "Session is unavailable", category=ErrorCategory.not_found)
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.session_read
            )
        else:
            parent = SessionRecord(
                id=new_object_id("session"),
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                labels=body.session_labels,
                created_at=now,
                updated_at=now,
            )
            session.add(parent)
            await session.flush()
        try:
            labels = merge_labels(parent.labels, body.labels)
        except ValueError as error:
            raise ApplicationError(
                "merged_labels_invalid",
                str(error),
                category=ErrorCategory.invalid_request,
            ) from error
        thread = Thread(
            id=new_thread_id(),
            version=1,
            queue_version=0,
            organization_id=workspace.organization_id,
            session_id=parent.id,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            default_environment_id=environment_id,
            default_environment_working_directory=working_directory,
            labels=labels,
            created_at=now,
            updated_at=now,
        )
        session.add(thread_record(thread))
        await session.flush()
        session.add(
            evidence_record(
                actor=actor,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                operation="thread.create",
                scope_id=workspace_id,
                identity=identity,
                result_kind="thread",
                result_ref=thread.id,
                now=now,
                response=thread,
            )
        )
        return thread

    try:
        return await (admission or OnlineAdmission(sessions)).commit(accept)
    except IntegrityError as error:
        if is_evidence_unique_race(error):
            async with transaction(sessions) as session:
                await authorize_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.agent_invoke
                )
                replay = await load_replay(
                    session,
                    actor=actor,
                    operation="thread.create",
                    scope_id=workspace_id,
                    identity=identity,
                    now=utc_now(),
                )
                if replay is not None:
                    row = await session.get(ThreadRecord, replay.result_ref)
                    if row is not None:
                        return replay.restore(Thread)
        raise
