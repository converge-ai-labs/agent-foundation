"""Paged application evidence; reads never connect to or acquire an Envd Session."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import and_, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.resources.rows import find_row
from a13n_service.runs.environments.access import find as find_environment
from a13n_service.runs.environments.bindings import SessionHead
from a13n_service.runs.environments.contexts import live_owner
from a13n_service.runs.environments.egress.tables import SessionCredentialRow
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadExecutionContextRow, ThreadSessionBindingRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

type ApplicationStatus = Literal["applied", "pending", "retry", "on_next_use", "on_create", "unknown"]


class SessionCredential(BaseModel):
    model_config = ConfigDict(frozen=True)

    thread_id: str
    environment_id: str
    environment_name: str
    binding_name: str
    session_version: int
    env: str
    credential_id: str
    credential_name: str
    credential_state: Literal["active", "revoked"]
    credential_version: int
    inject_hosts: list[str]
    status: ApplicationStatus
    # A tracked execution can be between commands; it does not prove a native connection is open.
    execution_active: bool
    native_session_id: str | None
    applied_credential_version: int | None
    applied_session_id: str | None
    applied_device_generation: str | None
    applied_at: datetime | None
    error_code: str | None = None
    retry_at: datetime | None = None


class SessionCredentialPage(BaseModel):
    items: list[SessionCredential]
    next_cursor: str | None


_Position = TypeAdapter(
    tuple[
        Annotated[str, Field(min_length=1, max_length=72)],
        Annotated[str, Field(min_length=1, max_length=72)],
        Annotated[str, Field(min_length=1, max_length=63)],
        Annotated[int, Field(ge=1, le=9223372036854775807)],
        Annotated[str, Field(min_length=1, max_length=128)],
    ]
)


def _position(row: SessionCredentialRow) -> tuple[str, str, str, int, str]:
    return row.thread_id, row.environment_id, row.binding_name, row.session_version, row.env


def _status(
    row: SessionCredentialRow, head: SessionHead | None, version: int, active: bool, environment_usable: bool
) -> ApplicationStatus:
    if head is None or not environment_usable:
        return "unknown"
    if head.session_version != row.session_version or head.session is None:
        return "on_create" if row.session_version == head.version else "unknown"
    if head.session.status != "active" or head.version != row.session_version:
        return "unknown"
    descriptor = head.session.descriptor
    sync = head.egress_sync
    failed = (
        sync is not None
        and (sync.session_id, sync.device_generation) == (descriptor.session_id, descriptor.generation)
        and sync.failures > 0
    )
    if (
        not failed
        and row.applied_credential_version == version
        and row.applied_session_id == descriptor.session_id
        and row.applied_device_generation == descriptor.generation
    ):
        return "applied"
    if not active:
        return "on_next_use"
    return "retry" if failed else "pending"


async def credential_sessions(
    storage: Storage, actor: Principal, workspace_id: str, credential_id: str, *, limit: int, cursor: str | None
) -> SessionCredentialPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await find_row(session, actor, CredentialRow, scope, credential_id, "read")
        return await _page(session, scope, credential_id=credential_id, limit=limit, cursor=cursor)


async def environment_sessions(
    storage: Storage, actor: Principal, workspace_id: str, environment_id: str, *, limit: int, cursor: str | None
) -> SessionCredentialPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await find_environment(session, scope, environment_id)
        return await _page(session, scope, environment_id=environment_id, limit=limit, cursor=cursor)


async def _page(
    session: AsyncSession,
    scope: WorkspaceScope,
    *,
    credential_id: str | None = None,
    environment_id: str | None = None,
    limit: int,
    cursor: str | None,
) -> SessionCredentialPage:
    row, binding, context = SessionCredentialRow, ThreadSessionBindingRow, ThreadExecutionContextRow
    owner = cursors.query_owner(scope.workspace_id, credential_id, environment_id)
    kind = "session_credentials"
    order = (row.thread_id, row.environment_id, row.binding_name, row.session_version, row.env)
    selected_mount = func.jsonb_build_array(
        func.jsonb_build_object(
            "environment_id",
            row.environment_id,
            "session_binding",
            row.binding_name,
            "session_version",
            row.session_version,
        )
    )
    query = (
        select(
            row,
            binding.head,
            EnvironmentRow.name,
            and_(
                EnvironmentRow.status.not_in(("stopping", "stopped", "deleting", "deleted", "disabled")),
                ~func.coalesce(EnvironmentRow.failure["permanent"].as_boolean(), False),
            ),
            CredentialRow.name,
            CredentialRow.state,
            CredentialRow.credential_version,
            live_owner(row.thread_id, context.worker_id, mount=selected_mount),
        )
        .join(
            binding,
            and_(
                binding.thread_id == row.thread_id,
                binding.environment_id == row.environment_id,
                binding.name == row.binding_name,
            ),
        )
        .join(EnvironmentRow, EnvironmentRow.id == row.environment_id)
        .join(CredentialRow, CredentialRow.id == row.credential_id)
        .outerjoin(context, context.thread_id == row.thread_id)
        .where(row.workspace_id == scope.workspace_id)
    )
    if credential_id is not None:
        query = query.where(row.credential_id == credential_id)
    if environment_id is not None:
        query = query.where(row.environment_id == environment_id)
    if cursor is not None:
        try:
            position = _Position.validate_python(tuple(cursors.decode(cursor, kind, owner)), strict=True)
        except ValidationError:
            raise ServiceError("invalid_cursor", "Invalid collection cursor") from None
        query = query.where(tuple_(*order) > tuple_(*map(literal, position)))
    rows = (await session.execute(query.order_by(*order).limit(limit + 1))).all()
    items = []
    for reference, raw_head, env_name, env_usable, name, state, version, active in rows[:limit]:
        head = SessionHead.model_validate(raw_head) if raw_head is not None else None
        status = _status(reference, head, version, active, env_usable)
        sync = head.egress_sync if head is not None and status == "retry" else None
        native = head.session if head is not None and head.session_version == reference.session_version else None
        items.append(
            SessionCredential(
                thread_id=reference.thread_id,
                environment_id=reference.environment_id,
                environment_name=env_name,
                binding_name=reference.binding_name,
                session_version=reference.session_version,
                env=reference.env,
                credential_id=reference.credential_id,
                credential_name=name,
                credential_state=state,
                credential_version=version,
                inject_hosts=reference.inject_hosts,
                status=status,
                execution_active=active,
                native_session_id=native.descriptor.session_id if native is not None else None,
                applied_credential_version=reference.applied_credential_version,
                applied_session_id=reference.applied_session_id,
                applied_device_generation=str(reference.applied_device_generation)
                if reference.applied_device_generation is not None
                else None,
                applied_at=reference.applied_at,
                error_code=sync.error_code if sync is not None else None,
                retry_at=sync.retry_at if sync is not None else None,
            )
        )
    next_cursor = cursors.encode(kind, owner, *_position(rows[limit - 1][0])) if len(rows) > limit else None
    return SessionCredentialPage(items=items, next_cursor=next_cursor)
