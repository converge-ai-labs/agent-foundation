"""Typed handback hints through the existing outbox, inbox and acceptance path."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbox import Claim, Handler, settle
from a13n_service.runs import inbox
from a13n_service.runs.browsers import lifecycle
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserRow, ThreadBrowserRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize


async def suppress(session: AsyncSession, thread_id: str) -> None:
    await session.execute(
        update(BrowserBlockedRow)
        .where(
            BrowserBlockedRow.thread_id == thread_id,
        )
        .values(suppressed=True)
    )


async def eligible(session: AsyncSession, runtime: Runtime, registration: BrowserBlockedRow) -> RunRow | None:
    row = await session.get(BrowserRow, registration.browser_id)
    origin = await session.get(RunRow, registration.run_id)
    thread = await session.get(ThreadRow, registration.thread_id)
    binding = await session.get(ThreadBrowserRow, registration.thread_id)
    current = await now(session)
    if (
        registration.suppressed
        or registration.expires_at is None
        or registration.expires_at <= current
        or row is None
        or row.status != "ready"
        or row.control != "automation"
        or row.generation != registration.generation
        or row.episode != registration.episode
        or (row.expires_at is not None and row.expires_at <= current)
        or origin is None
        or origin.cancel_requested_at is not None
        or origin.status == "cancelled"
        or thread is None
        or thread.archived_at is not None
        or binding is None
        or binding.browser_id != row.id
    ):
        return None
    scope = WorkspaceScope(row.organization_id, row.workspace_id)
    authority = ExecutionAuthority.model_validate(origin.authority)
    try:
        principal = await principal_for(session, runtime.access, origin.principal_id, confinement=scope)
        authorize(principal, scope, "run", authority=authority)
        await lifecycle.authorize_use(session, principal, row, scope, authority=authority)
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        return None
    return origin


class StaleNotice(ServiceError):
    """An unincorporated handback lost eligibility; discard the input, not the Thread's work."""

    def __init__(self, entry_id: str):
        super().__init__(
            "conflict",
            "Browser handback is no longer current",
            {"kind": "inbox_entry", "id": entry_id, "reason": "browser_notice_stale"},
        )
        self.entry_id = entry_id


async def require_notice(session: AsyncSession, runtime: Runtime, entry: InboxEntryRow) -> None:
    registration = await session.get(BrowserBlockedRow, entry.payload["registration_id"])
    if registration is not None:
        # Acceptance and input reads share the delivery/retake serialization point.
        # Refresh after the Browser lock: another Thread may have suppressed this row while we waited.
        await lock(session, BrowserRow, registration.browser_id)
        await session.refresh(registration)
    if (
        registration is None
        or registration.entry_id != entry.id
        or await eligible(session, runtime, registration) is None
    ):
        raise StaleNotice(entry.id)


def handbacks(runtime: Runtime) -> Handler:
    async def deliver(claimed: Claim) -> None:
        async with transaction(runtime.storage) as session:
            thread = await lock(session, ThreadRow, claimed.target["thread_id"])
            registration = await session.get(BrowserBlockedRow, claimed.payload["registration_id"])
            if thread is None or registration is None or registration.thread_id != thread.id:
                await settle(session, claimed, "dead", error="invalid_target")
                return
            # Browser lock linearizes delivery with retake. Never lock registration before Browser.
            await lock(session, BrowserRow, registration.browser_id)
            await session.refresh(registration)
            if registration.entry_id is not None:
                await settle(session, claimed, "delivered")
                return
            origin = await eligible(session, runtime, registration)
            if origin is None:
                if await settle(session, claimed, "delivered", error="continuation_suppressed"):
                    registration.suppressed = True
                return
            payload = {
                "registration_id": registration.id,
                "browser_id": registration.browser_id,
                "generation": registration.generation,
                "episode": registration.episode,
            }
            if not await inbox.has_room(
                session, thread.id, runtime.settings.control, adding=1, growth=inbox.payload_size(payload)
            ):
                await settle(session, claimed, "deferred", error="inbox_full", retry_after=30)
                return
            if not await settle(session, claimed, "delivered"):
                return
            entry = await inbox.append_browser_handback(session, thread, origin, payload)
            registration.entry_id = entry.id
            thread_id = thread.id
        from a13n_service.runs.accept import advance

        await advance(runtime, thread_id)

    return deliver
