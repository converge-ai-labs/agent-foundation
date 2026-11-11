"""Paged application evidence; reads never connect to or acquire an Envd Session."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import BigInteger, Numeric, and_, case, func, literal, or_, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

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

# A binding is applied only if every selected reference is applied. Unconfirmed evidence takes precedence.
_STATUS_PRIORITY: tuple[ApplicationStatus, ...] = ("applied", "on_create", "on_next_use", "pending", "retry", "unknown")


class SessionApplicationSummary(BaseModel):
    """All matching binding versions, counted once each independently of page size and cursor."""

    applied: int = Field(ge=0)
    pending: int = Field(ge=0)
    retry: int = Field(ge=0)
    on_next_use: int = Field(ge=0)
    on_create: int = Field(ge=0)
    unknown: int = Field(ge=0)


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
    summary: SessionApplicationSummary


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


def _status(active: ColumnElement[bool]) -> ColumnElement[str]:
    """One SQL classification serves both the bounded detail page and the full collection summary."""
    row, head = SessionCredentialRow, ThreadSessionBindingRow.head
    native, sync = head["session"], head["egress_sync"]
    descriptor = native["descriptor"]
    session_id = descriptor["session_id"].as_string()
    generation = descriptor["generation"].as_string().cast(Numeric(20, 0))
    head_version = head["version"].as_string().cast(BigInteger)
    failed = func.coalesce(
        and_(
            sync["session_id"].as_string() == session_id,
            sync["device_generation"].as_string().cast(Numeric(20, 0)) == generation,
            sync["failures"].as_integer() > 0,
        ),
        False,
    )
    return case(
        (
            or_(
                head_version.is_(None),
                EnvironmentRow.status.in_(("stopping", "stopped", "deleting", "deleted", "disabled")),
                func.coalesce(EnvironmentRow.failure["permanent"].as_boolean(), False),
            ),
            "unknown",
        ),
        (
            or_(
                head["session_version"].as_string().cast(BigInteger).is_distinct_from(row.session_version),
                session_id.is_(None),
            ),
            case((row.session_version == head_version, "on_create"), else_="unknown"),
        ),
        (
            or_(func.coalesce(native["status"].as_string(), "active") != "active", head_version != row.session_version),
            "unknown",
        ),
        (
            and_(
                ~failed,
                row.applied_credential_version == CredentialRow.credential_version,
                row.applied_session_id == session_id,
                row.applied_device_generation == generation,
            ),
            "applied",
        ),
        (~active, "on_next_use"),
        (failed, "retry"),
        else_="pending",
    )


def _summary(evidence: CTE):
    key = (evidence.c.thread_id, evidence.c.environment_id, evidence.c.binding_name, evidence.c.session_version)
    priority = case({status: rank for rank, status in enumerate(_STATUS_PRIORITY)}, value=evidence.c.status)
    bindings = select(func.max(priority).label("priority")).group_by(*key).subquery()
    return select(
        *(
            func.count().filter(bindings.c.priority == rank).label(status)
            for rank, status in enumerate(_STATUS_PRIORITY)
        )
    ).subquery()


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
    active = live_owner(row.thread_id, context.worker_id, mount=selected_mount)
    binding_match = and_(
        binding.thread_id == row.thread_id,
        binding.environment_id == row.environment_id,
        binding.name == row.binding_name,
    )
    query = (
        select(
            *order,
            active.label("active"),
            _status(active).label("status"),
        )
        .join(binding, binding_match)
        .join(EnvironmentRow, EnvironmentRow.id == row.environment_id)
        .join(CredentialRow, CredentialRow.id == row.credential_id)
        .outerjoin(context, context.thread_id == row.thread_id)
        .where(row.workspace_id == scope.workspace_id)
    )
    if credential_id is not None:
        query = query.where(row.credential_id == credential_id)
    if environment_id is not None:
        query = query.where(row.environment_id == environment_id)
    # The narrow evidence CTE and both consumers share one PostgreSQL statement snapshot.
    # Only the requested page loads configuration and descriptive metadata into the Service.
    evidence = query.cte("credential_evidence")
    summary = _summary(evidence)
    page_order = tuple(evidence.c[column.key] for column in order)
    selected = select(evidence)
    if cursor is not None:
        try:
            position = _Position.validate_python(tuple(cursors.decode(cursor, kind, owner)), strict=True)
        except ValidationError:
            raise ServiceError("invalid_cursor", "Invalid collection cursor") from None
        selected = selected.where(tuple_(*page_order) > tuple_(*map(literal, position)))
    page = selected.order_by(*page_order).limit(limit + 1).subquery()
    page_key = tuple(page.c[column.key] for column in order)
    details = (
        select(
            row,
            binding.head,
            EnvironmentRow.name,
            CredentialRow.name,
            CredentialRow.state,
            CredentialRow.credential_version,
            page.c.active,
            page.c.status,
            *summary.c,
        )
        .select_from(summary)
        .outerjoin(page, true())
        .outerjoin(row, tuple_(*order) == tuple_(*page_key))
        .outerjoin(binding, binding_match)
        .outerjoin(EnvironmentRow, EnvironmentRow.id == row.environment_id)
        .outerjoin(CredentialRow, CredentialRow.id == row.credential_id)
        .order_by(*page_key)
    )
    result = (await session.execute(details)).all()
    counts = SessionApplicationSummary.model_validate(
        {status: result[0]._mapping[status] for status in _STATUS_PRIORITY}
    )
    rows = [item for item in result if item[0] is not None]
    items = []
    for reference, raw_head, env_name, name, state, version, active, status, *_counts in rows[:limit]:
        head = SessionHead.model_validate(raw_head) if raw_head is not None else None
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
    return SessionCredentialPage(items=items, next_cursor=next_cursor, summary=counts)
