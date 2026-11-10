"""Accept every organization entry path through ordinary Run admission."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import conflict, disabled
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.memories.tables import MemoryRow, MemorySpaceRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.accept import Source, start_run
from a13n_service.runs.inbox import append_message
from a13n_service.runs.memories import candidates, organization_history, organizer
from a13n_service.runs.memories.organization_schemas import (
    MemoryOrganization,
    MemoryOrganizationPage,
    OrganizationRequest,
    OrganizationSource,
    SpaceTarget,
)
from a13n_service.runs.memories.organization_tables import OrganizationRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Message, MessagePayload, RunOptions, TextPart, UsageLimit
from a13n_service.runs.sessions import new_session
from a13n_service.runs.submit import validate_message
from a13n_service.runs.tables import SessionRow
from a13n_service.runs.threads import new_thread
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize, execution_authority


def view(row: OrganizationRow) -> MemoryOrganization:
    assert row.run_id is not None
    return MemoryOrganization(
        id=row.id,
        workspace_id=row.workspace_id,
        request=OrganizationRequest.model_validate(row.request),
        sources=[OrganizationSource.model_validate(item) for item in row.sources],
        candidate_memory_id=row.candidate_memory_id,
        session_id=row.session_id,
        thread_id=row.thread_id,
        run_id=row.run_id,
        created_by_id=row.created_by_id,
        created_at=row.created_at,
    )


async def admit(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    request: OrganizationRequest,
    *,
    authority: ExecutionAuthority,
    owner: SessionRow | None = None,
    history_sources: list[OrganizationSource] | None = None,
) -> OrganizationRow:
    """The caller owns one short transaction; no execution or external I/O happens here."""
    for verb in ("run", "read", "write"):
        authorize(actor, scope, verb, authority=authority)
    space_id = None
    if isinstance(request.target, SpaceTarget):
        space = await find_row(session, actor, MemorySpaceRow, scope, request.target.id, "write", lock=True)
        if not space.enabled:
            raise disabled(MemorySpaceRow.KIND, space.id)
        memory_id, space_id = space.memory_id, space.id
    else:
        memory = await find_row(session, actor, MemoryRow, scope, request.target.id, "write", lock=True)
        if memory.kind != "file":
            raise conflict(MemoryRow.KIND, memory.id, "file_memory_required")
        managed = await session.scalar(select(MemorySpaceRow.id).where(MemorySpaceRow.memory_id == memory.id))
        if managed is not None:
            raise conflict(MemoryRow.KIND, memory.id, "managed_memory", space_id=managed)
        memory_id = memory.id
    candidate_memory_id, baseline = None, None
    if request.output == "candidate":
        target = await find_row(session, actor, MemoryRow, scope, memory_id, "read")
        candidate_memory_id, baseline = await candidates.prepare(session, runtime, actor, scope, target)
    sources = (
        await organization_history.select_sources(session, actor, scope, request.history)
        if history_sources is None
        else history_sources
    )
    agent_id, revision_id = await organizer.revision(
        session, runtime, actor, scope, request.model, history=request.history is not None
    )
    if owner is None:
        owner = new_session(scope.organization_id, scope.workspace_id, actor.id)
        owner.purpose = "memory_organization"
        session.add(owner)
        await session.flush()
    thread = new_thread(owner, mcp_headers={})
    thread.purpose = "memory_organization"
    session.add(thread)
    await session.flush()
    organization = OrganizationRow(
        id=new_object_id("morg"),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        request=request.model_dump(mode="json"),
        sources=[item.model_dump(mode="json") for item in sources],
        memory_id=memory_id,
        space_id=space_id,
        candidate_memory_id=candidate_memory_id,
        baseline=baseline,
        application=None,
        discarded_at=None,
        session_id=owner.id,
        thread_id=thread.id,
        run_id=None,
        created_by_id=actor.id,
    )
    session.add(organization)
    await session.flush()
    message = Message(
        agent_id=agent_id,
        agent_revision_id=revision_id,
        payload=MessagePayload(content=(TextPart(type="text", text=request.instructions),)),
        options=RunOptions(max_usage=UsageLimit(requests=200)),
    )
    revision, overrides, options = await validate_message(
        session, runtime, actor, scope, thread, message, authority=authority
    )
    entry = await append_message(
        session,
        thread,
        message,
        principal_id=actor.id,
        authority=authority,
        request=None,
        control=runtime.settings.control,
    )
    run = await start_run(
        session, runtime, thread, Source.message(entry, "input", actor, revision, overrides, options=options)
    )
    organization.run_id = run.id
    await session.flush()
    audit_row(session, actor, organization, "create")
    return organization


async def create(
    runtime: Runtime, actor: Principal, workspace_id: str, request: OrganizationRequest
) -> MemoryOrganization:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = await admit(session, runtime, actor, scope, request, authority=execution_authority(actor, scope))
        return view(row)


async def get(runtime: Runtime, actor: Principal, workspace_id: str, organization_id: str) -> MemoryOrganization:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return view(await find_row(session, actor, OrganizationRow, scope, organization_id, "read"))


async def listing(
    runtime: Runtime, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> MemoryOrganizationPage:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.keyset_page(
            session,
            select(OrganizationRow).where(OrganizationRow.workspace_id == scope.workspace_id),
            (OrganizationRow.created_at, OrganizationRow.id),
            kind="memory_organizations",
            owner=workspace_id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return MemoryOrganizationPage(items=[view(row) for row in rows], next_cursor=next_cursor)
