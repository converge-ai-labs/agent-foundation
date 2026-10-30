"""Browser resource operations and desired Thread bindings."""

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import validate_cdp_endpoint
from a13n_service.resources.connections.headers import normalize_headers
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import BrowserProviderRow
from a13n_service.runs.browsers import lifecycle
from a13n_service.runs.browsers.schemas import Browser, BrowserAttach, BrowserBinding, BrowserCreate, BrowserPage
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserRow, ThreadBrowserRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.threads import get_thread, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope


def audit(session: AsyncSession, actor: Principal, row: BrowserRow, verb: str) -> None:
    record(
        session,
        WorkspaceScope(row.organization_id, row.workspace_id),
        actor_id=actor.id,
        action=f"browser.{verb}",
        target_kind="browser",
        target_id=row.id,
    )


async def create(runtime: Runtime, actor: Principal, workspace_id: str, body: BrowserCreate) -> Browser:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        account = await resolve_provider(session, actor, BrowserProviderRow, scope, body.provider_id)
        definition = runtime.registry.get("browser", account.type)
        try:
            recipe = definition.recipe_model.model_validate(body.recipe).model_dump(mode="json")
        except ValidationError:
            raise invalid("recipe", "Recipe does not match the Browser provider schema") from None
        row = BrowserRow(
            id=new_object_id("browser"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=account.id,
            name=body.name,
            recipe=recipe,
            provider_identity=lifecycle.identity(runtime, account),
            idle_seconds=body.idle_seconds,
            created_by_id=actor.id,
        )
        session.add(row)
        await session.flush()
        audit(session, actor, row, "create")
        browser_id = row.id
    if body.prepare:
        await lifecycle.prepare(runtime, workspace_id, browser_id)
    return await get(runtime, actor, workspace_id, browser_id)


async def attach(runtime: Runtime, actor: Principal, workspace_id: str, body: BrowserAttach) -> Browser:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        try:
            await validate_cdp_endpoint(runtime.endpoint_policy, body.cdp_url.get_secret_value())
            headers = normalize_headers(
                {name: value.get_secret_value() for name, value in body.headers.items()}, authentication=True
            )
        except ValueError:
            raise invalid("connection", "CDP destination or headers are not permitted") from None
        row = BrowserRow(
            id=new_object_id("browser"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            source="external_cdp",
            provider_id=None,
            name=body.name,
            recipe={},
            provider_identity={},
            idle_seconds=body.idle_seconds,
            created_by_id=actor.id,
        )
        row.handle = lifecycle.protect(
            runtime, row, "handle", {"cdp_url": body.cdp_url.get_secret_value(), "headers": headers}
        )
        session.add(row)
        await session.flush()
        audit(session, actor, row, "attach")
        browser_id = row.id
    if body.prepare:
        await lifecycle.prepare(runtime, workspace_id, browser_id)
    return await get(runtime, actor, workspace_id, browser_id)


async def get(runtime: Runtime, actor: Principal, workspace_id: str, browser_id: str) -> Browser:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        return Browser.model_validate(await lifecycle.find(session, workspace_id, browser_id))


async def list_browsers(
    runtime: Runtime, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> BrowserPage:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(BrowserRow).where(BrowserRow.workspace_id == workspace_id, BrowserRow.status != "deleted"),
            BrowserRow.id,
            kind="browsers",
            owner=workspace_id,
            limit=limit,
            cursor=cursor,
        )
        return BrowserPage(items=[Browser.model_validate(row) for row in rows], next_cursor=next_cursor)


async def prepare(runtime: Runtime, actor: Principal, workspace_id: str, browser_id: str) -> Browser:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = await lifecycle.find(session, workspace_id, browser_id)
        await lifecycle.authorize_use(session, actor, row, scope)
        external = row.source == "external_cdp"
    await lifecycle.prepare(runtime, workspace_id, browser_id, inspect_ready=external)
    return await get(runtime, actor, workspace_id, browser_id)


async def release(
    runtime: Runtime, actor: Principal, workspace_id: str, browser_id: str, *, if_match: str | None
) -> Browser:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        require_match(if_match, row.id, row.version)
        if row.status == "deleted":
            return Browser.model_validate(row)
        # Explicit retirement fences future use; it does not claim unknown calls completed.
        # Idle release retains the default unsettled-operation gate.
        retiring_return = row.source == "managed" and row.control == "returning"
        if row.status != "lost" and await lifecycle.in_use(
            session,
            row,
            include_unsettled=False,
            include_control=not retiring_return,
            include_notices=not retiring_return,
        ):
            raise conflict("browser", browser_id, "browser_in_use")
        if row.claim_until is not None and row.claim_until > await now(session):
            raise conflict("browser", browser_id, "browser_operation_pending")
        if retiring_return:
            await session.execute(
                update(BrowserBlockedRow).where(BrowserBlockedRow.browser_id == row.id).values(suppressed=True)
            )
        lifecycle.release_intent(row)
        audit(session, actor, row, "release")
    await lifecycle.reconcile(runtime, workspace_id, browser_id)
    return await get(runtime, actor, workspace_id, browser_id)


async def binding(runtime: Runtime, actor: Principal, workspace_id: str, thread_id: str) -> BrowserBinding | None:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        await get_thread(session, workspace_id, thread_id)
        row = await session.get(ThreadBrowserRow, thread_id)
        return BrowserBinding.model_validate(row) if row else None


async def bind(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    body: BrowserBinding | None,
    *,
    if_match: str | None,
) -> BrowserBinding | None:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, workspace_id, thread_id, lock=True)
        require_open(thread)
        require_match(if_match, thread.id, thread.version)
        previous = await session.get(ThreadBrowserRow, thread_id)
        if body is not None:
            row = await lifecycle.find(session, workspace_id, body.browser_id, lock=True)
            lifecycle.usable(row)
            await lifecycle.authorize_use(session, actor, row, scope)
        if previous is not None:
            await session.delete(previous)
            await session.flush()
        if body is not None:
            session.add(ThreadBrowserRow(thread_id=thread_id, workspace_id=workspace_id, **body.model_dump()))
        # Rebinding ends pending auto-continuation, but never closes borrowed tabs.
        await session.execute(
            update(BrowserBlockedRow).where(BrowserBlockedRow.thread_id == thread_id).values(suppressed=True)
        )
        thread.updated_at = await now(session)
        await session.flush()
        return body


async def freeze(
    session: AsyncSession,
    actor: Principal,
    thread: ThreadRow,
    authority: ExecutionAuthority,
    *,
    inherited: RunRow | None = None,
) -> dict | None:
    """Resume keeps its exact binding. New Runs freeze desired state; children/forks opt in explicitly."""
    if inherited is not None:
        selected = BrowserBinding.model_validate(inherited.browser_binding) if inherited.browser_binding else None
    else:
        row = await session.get(ThreadBrowserRow, thread.id)
        selected = BrowserBinding.model_validate(row) if row else None
    if selected is None:
        return None
    browser = await lifecycle.find(session, thread.workspace_id, selected.browser_id, lock=True)
    lifecycle.usable(browser)
    await lifecycle.authorize_use(
        session, actor, browser, WorkspaceScope(thread.organization_id, thread.workspace_id), authority=authority
    )
    browser.last_used_at = await now(session)
    return selected.model_dump(mode="json")
