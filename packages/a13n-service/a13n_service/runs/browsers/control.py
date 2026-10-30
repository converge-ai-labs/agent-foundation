"""Browser-wide human control, with durable gates before any supplier input grant."""

from datetime import timedelta

import anyio
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import enqueue_once
from a13n_service.providers.browser.definition import BrowserProvider, ViewerGrant
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import BrowserProviderRow
from a13n_service.runs.browsers import lifecycle, service
from a13n_service.runs.browsers.schemas import Browser, Takeover, Viewer
from a13n_service.runs.browsers.tables import (
    BrowserBlockedRow,
    BrowserRow,
    BrowserSelectionRow,
    BrowserTabRow,
    ThreadBrowserRow,
)
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ThreadRow
from a13n_service.runs.threads import get_thread, require_open
from a13n_service.tenancy.access import principal_for, workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, authorize


def require_viewer(row: BrowserRow) -> None:
    if row.source == "external_cdp":
        raise conflict("browser", row.id, "browser_viewer_unsupported")


async def takeover(
    runtime: Runtime, actor: Principal, workspace_id: str, browser_id: str, body: Takeover, *, if_match: str | None
) -> Browser:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, workspace_id, body.thread_id, lock=True)
        require_open(thread)
        binding = await session.get(ThreadBrowserRow, thread.id)
        if binding is None or binding.browser_id != browser_id:
            raise conflict("thread", thread.id, "browser_binding_missing")
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        require_match(if_match, row.id, row.version)
        lifecycle.usable(row)
        require_viewer(row)
        assert row.provider_id is not None
        account = await resolve_provider(session, actor, BrowserProviderRow, scope, row.provider_id)
        if not runtime.registry.get("browser", account.type).supports_viewer:
            raise conflict("browser", row.id, "browser_viewer_unsupported")
        if row.control != "automation":
            raise conflict("browser", row.id, "browser_control_pending")
        row.control = "draining"
        row.episode += 1
        row.controller_id, row.controller_thread_id = actor.id, thread.id
        row.viewer_id = new_object_id("bview")
        row.viewer_revoked = False
        row.viewer = row.human_baseline = None
        row.observation_epoch += 1
        await session.execute(
            update(BrowserBlockedRow)
            .where(
                BrowserBlockedRow.browser_id == row.id,
            )
            .values(suppressed=True)
        )
        service.audit(session, actor, row, "takeover")
    # Readiness may allocate; the browser gate is already closed.
    await lifecycle.prepare(runtime, workspace_id, browser_id)
    await reconcile(runtime, workspace_id, browser_id)
    return await service.get(runtime, actor, workspace_id, browser_id)


async def handback(
    runtime: Runtime, actor: Principal, workspace_id: str, browser_id: str, *, if_match: str | None
) -> Browser:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        require_match(if_match, row.id, row.version)
        lifecycle.usable(row)
        require_viewer(row)
        if row.control == "automation":
            return Browser.model_validate(row)
        # The baseline is persisted under the claim/control fence before grant dispatch.
        # Cancel only an unclaimed intent that cannot have granted input, even if ready.
        if row.control == "draining" and row.human_baseline is None and row.viewer is None:
            if row.claim_until is not None and row.claim_until > await now(session):
                raise conflict("browser", browser_id, "browser_operation_pending")
            row.control = "automation"
            row.viewer_id = row.controller_id = row.controller_thread_id = None
            row.observation_epoch += 1
            await session.execute(
                update(BrowserBlockedRow).where(BrowserBlockedRow.browser_id == row.id).values(suppressed=True)
            )
        else:
            # Cleanup remains available even when the provider account has been disabled.
            row.control = "returning"
        service.audit(session, actor, row, "handback")
    await reconcile(runtime, workspace_id, browser_id)
    return await service.get(runtime, actor, workspace_id, browser_id)


async def viewer(runtime: Runtime, actor: Principal, workspace_id: str, browser_id: str) -> Viewer:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = await lifecycle.find(session, workspace_id, browser_id)
        lifecycle.usable(row)
        require_viewer(row)
        assert row.provider_id is not None
        if row.expires_at is not None and row.expires_at <= await now(session):
            raise conflict("browser", row.id, "browser_expired")
        await resolve_provider(session, actor, BrowserProviderRow, scope, row.provider_id)
        if row.control != "human" or row.controller_id != actor.id or row.viewer is None:
            raise conflict("browser", row.id, "browser_viewer_unavailable")
        value = Viewer.model_validate(lifecycle.reveal(runtime, row, "viewer", row.viewer))
        if value.expires_at <= await now(session):
            raise conflict("browser", row.id, "browser_viewer_expired")
        return value


async def inventory(provider: BrowserProvider, state: dict) -> set[str]:
    browser = await provider.connect(state)
    try:
        infos = (await browser.send("Target.getTargets"))["targetInfos"]
        return {info["targetId"] for info in infos if info["type"] == "page"}
    finally:
        with anyio.CancelScope(shield=True), anyio.move_on_after(10):
            await browser.close()


async def controller_allowed(session: AsyncSession, runtime: Runtime, row: BrowserRow) -> bool:
    if row.controller_id is None or row.controller_thread_id is None:
        return False
    thread = await session.get(ThreadRow, row.controller_thread_id)
    if thread is None or thread.archived_at is not None:
        return False
    scope = WorkspaceScope(row.organization_id, row.workspace_id)
    try:
        actor = await principal_for(session, runtime.access, row.controller_id, confinement=scope)
        authorize(actor, scope, "run")
        await lifecycle.authorize_use(session, actor, row, scope)
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        return False
    return True


async def reconcile(runtime: Runtime, workspace_id: str, browser_id: str) -> None:
    claim = new_object_id("bclaim")
    async with transaction(runtime.storage) as session:
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        current = await now(session)
        if await lifecycle.expire(session, row):
            return
        if row.status != "ready" or row.control == "automation":
            return
        if row.claim_until is not None and row.claim_until > current:
            return
        if row.control in {"draining", "human"}:
            expired = (
                row.viewer is not None
                and Viewer.model_validate(lifecycle.reveal(runtime, row, "viewer", row.viewer)).expires_at <= current
            )
            allowed = await controller_allowed(session, runtime, row)
            if row.control == "human" and (row.viewer_revoked or (row.viewer is not None and allowed and not expired)):
                return
            if not allowed or expired:
                # Revoke human input, but keep browser tools gated until an explicit handback.
                row.control = "human"
                row.viewer = None
        if row.control != "human" and await lifecycle.unsettled(session, row.id):
            row.failure = "browser_operations_unsettled"
            return
        selected = await lifecycle.target(session, runtime, row)
        assert selected.account is not None
        phase, episode, grant_id = row.control, row.episode, row.viewer_id
        assert grant_id is not None and selected.state is not None
        baseline = row.human_baseline
        owner = row.controller_thread_id
        row.claim_id = claim
        row.claim_until = current + timedelta(seconds=runtime.settings.providers.operation_seconds + 15)
    granted: ViewerGrant | None = None
    pages: set[str] = set()
    failure = None
    try:
        async with lifecycle.open_provider(runtime, selected.account) as provider:
            with anyio.fail_after(runtime.settings.providers.operation_seconds):
                if phase == "draining":
                    if not selected.account.enabled:
                        raise conflict("browser", browser_id, "browser_provider_disabled")
                    if baseline is None:
                        pages = await inventory(provider, selected.state)
                        async with transaction(runtime.storage) as session:
                            row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
                            if row.claim_id != claim or row.control != phase or row.episode != episode:
                                return
                            row.human_baseline = sorted(pages)
                    granted = await provider.grant_viewer(selected.state, grant_id)
                else:
                    await provider.revoke_viewer(selected.state, grant_id)
                    if phase == "returning":
                        pages = await inventory(provider, selected.state)
    except Exception:
        failure = "browser_viewer_grant_failed" if phase == "draining" else "browser_viewer_revoke_failed"
    async with transaction(runtime.storage) as session:
        await lifecycle.lock_owners(session, (owner,) if owner is not None else ())
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        if row.claim_id != claim:
            return
        row.claim_id = row.claim_until = None
        # A concurrent handback changes intent, never publishes a late grant or opens the gate.
        if row.status != "ready" or row.control != phase or row.episode != episode:
            return
        row.failure = failure
        if failure is not None:
            return
        if phase == "human":
            row.viewer_revoked = True
            row.viewer = None
            return
        if phase == "draining":
            assert granted is not None
            row.viewer = lifecycle.protect(
                runtime,
                row,
                "viewer",
                Viewer(
                    url=granted.url,
                    expires_at=granted.expires_at,
                    episode=episode,
                ).model_dump(mode="json"),
            )
            row.control = "human"
            return
        if row.human_baseline is not None and row.controller_thread_id is not None:
            for target_id in pages - set(row.human_baseline):
                await session.execute(
                    insert(BrowserTabRow)
                    .values(
                        browser_id=row.id,
                        workspace_id=workspace_id,
                        target_id=target_id,
                        thread_id=row.controller_thread_id,
                        generation=row.generation,
                    )
                    .on_conflict_do_nothing()
                )
        await session.execute(
            update(BrowserSelectionRow)
            .where(
                BrowserSelectionRow.browser_id == row.id,
                BrowserSelectionRow.target_id.not_in(pages),
            )
            .values(target_id=None)
        )
        row.viewer = row.human_baseline = None
        row.viewer_id = row.controller_id = row.controller_thread_id = None
        row.control = "automation"
        row.observation_epoch += 1
        row.last_used_at = await now(session)
        for registration in await session.scalars(
            select(BrowserBlockedRow).where(
                BrowserBlockedRow.browser_id == row.id,
                BrowserBlockedRow.generation == row.generation,
                BrowserBlockedRow.episode == episode,
                BrowserBlockedRow.suppressed.is_(False),
                BrowserBlockedRow.expires_at > row.last_used_at,
            )
        ):
            await enqueue_once(
                session,
                organization_id=row.organization_id,
                workspace_id=workspace_id,
                kind="browser_handback",
                dedupe_key=registration.id,
                target={"thread_id": registration.thread_id},
                payload={"registration_id": registration.id},
            )
