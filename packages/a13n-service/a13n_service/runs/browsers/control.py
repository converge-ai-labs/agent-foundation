"""Browser-wide human control, with durable gates before any supplier input grant."""

from datetime import datetime, timedelta

import anyio
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import enqueue_once
from a13n_service.providers.browser.definition import BrowserProvider, ViewerGrant
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import BrowserProviderRow
from a13n_service.runs.browsers import lifecycle, service
from a13n_service.runs.browsers.schemas import Browser, RenewControl, Takeover, Viewer
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


def lease_seconds(runtime: Runtime, requested: int | None) -> int:
    policy = runtime.settings.control
    seconds = policy.browser_control_seconds if requested is None else requested
    if not policy.browser_control_min_seconds <= seconds <= policy.browser_control_max_seconds:
        raise invalid(
            "lease_seconds",
            f"must be between {policy.browser_control_min_seconds} and {policy.browser_control_max_seconds}",
        )
    return seconds


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
        row.control_expires_at = await now(session) + timedelta(seconds=lease_seconds(runtime, body.lease_seconds))
        row.control = "draining"
        row.control_due_at = None
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
        await return_control(session, row, await now(session), explicit=True)
        service.audit(session, actor, row, "handback")
    await reconcile(runtime, workspace_id, browser_id)
    return await service.get(runtime, actor, workspace_id, browser_id)


async def renew(runtime: Runtime, actor: Principal, workspace_id: str, browser_id: str, body: RenewControl) -> Browser:
    """Each accepted keepalive extends from database time, never from the old deadline."""
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        lifecycle.usable(row)
        require_viewer(row)
        await lifecycle.authorize_use(session, actor, row, scope)
        current = await now(session)
        if (
            row.generation != body.generation
            or row.episode != body.episode
            or row.controller_id != actor.id
            or row.control not in {"draining", "human"}
        ):
            raise conflict("browser", browser_id, "browser_control_changed")
        previous_deadline = row.control_expires_at
        if previous_deadline is None or previous_deadline <= current:
            raise conflict("browser", browser_id, "browser_control_expired")
        if (row.expires_at is not None and row.expires_at <= current) or not await controller_allowed(
            session, runtime, row
        ):
            raise conflict("browser", browser_id, "browser_control_unavailable")
        if row.control == "human":
            grant = Viewer.model_validate(lifecycle.reveal(runtime, row, "viewer", row.viewer)) if row.viewer else None
            if grant is None or grant.expires_at <= current:
                raise conflict("browser", browser_id, "browser_viewer_expired")
        new_deadline = max(
            previous_deadline,
            current + timedelta(seconds=lease_seconds(runtime, body.lease_seconds)),
        )
        row.control_expires_at = new_deadline
        if new_deadline > previous_deadline:
            row.control_due_at = current
        await session.flush()
        result = Browser.model_validate(row)
    return result


async def return_control(session: AsyncSession, row: BrowserRow, current: datetime, *, explicit: bool = False) -> None:
    """Shared manual/expiry intent; only a proven pre-grant cancellation avoids revocation."""
    active_claim = row.claim_until is not None and row.claim_until > current
    if row.control == "draining" and row.human_baseline is None and row.viewer is None:
        if active_claim and explicit:
            raise conflict("browser", row.id, "browser_operation_pending")
        if not active_claim:
            row.control = "automation"
            row.control_expires_at = None
            row.viewer_id = row.controller_id = row.controller_thread_id = None
            row.claim_id = row.claim_until = None
            row.observation_epoch += 1
            await session.execute(
                update(BrowserBlockedRow).where(BrowserBlockedRow.browser_id == row.id).values(suppressed=True)
            )
            return
    # Changing intent fences publication immediately; an active claim still owns its I/O window.
    if row.control != "returning":
        row.control_due_at = None
    row.control = "returning"
    row.viewer = None


def owns_claim(row: BrowserRow, claim: str, generation: int, episode: int, phase: str, current: datetime) -> bool:
    return (
        row.claim_id == claim
        and row.claim_until is not None
        and row.claim_until > current
        and row.generation == generation
        and row.episode == episode
        and row.status == "ready"
        and row.control == phase
    )


def maintenance_interval(runtime: Runtime) -> float:
    return min(runtime.settings.providers.operation_scan_seconds, 5.0)


def recovery_window(runtime: Runtime) -> timedelta:
    # One crashed provider claim, a replacement open/operation/close, and polling slack.
    return timedelta(seconds=4 * runtime.settings.providers.operation_seconds + 35 + 2 * maintenance_interval(runtime))


def renewal_window(runtime: Runtime) -> timedelta:
    # A new short grant must leave room for crash recovery and a useful maintenance interval.
    return recovery_window(runtime) + timedelta(seconds=2 * maintenance_interval(runtime))


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
        current = await now(session)
        if (
            row.control != "human"
            or row.controller_id != actor.id
            or row.viewer is None
            or row.control_expires_at is None
            or row.control_expires_at <= current
            or not await controller_allowed(session, runtime, row)
        ):
            raise conflict("browser", row.id, "browser_viewer_unavailable")
        value = Viewer.model_validate(lifecycle.reveal(runtime, row, "viewer", row.viewer))
        value.expires_at = min(value.expires_at, row.control_expires_at)
        if value.expires_at <= current:
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


async def reconcile(runtime: Runtime, workspace_id: str, browser_id: str, lease: datetime | None = None) -> None:
    claim = new_object_id("bclaim")
    async with transaction(runtime.storage) as session:
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        current = await now(session)
        if await lifecycle.expire(session, row):
            return
        if row.control == "automation":
            return
        if row.control in {"draining", "human"}:
            previous = (
                Viewer.model_validate(lifecycle.reveal(runtime, row, "viewer", row.viewer)) if row.viewer else None
            )
            reason = None
            if row.control_expires_at is None or row.control_expires_at <= current:
                reason = "browser_control_expired"
            elif not await controller_allowed(session, runtime, row):
                reason = "browser_controller_unavailable"
            elif row.control == "human" and (previous is None or previous.expires_at <= current):
                reason = "browser_viewer_expired"
            if reason is not None:
                row.failure = reason
                await return_control(session, row, current)
            elif row.control == "human":
                assert previous is not None and row.control_expires_at is not None
                if previous.expires_at >= row.control_expires_at or previous.expires_at > current + recovery_window(
                    runtime
                ):
                    return
        if row.status != "ready" or row.control == "automation":
            return
        # Only the dispatcher that leased an already-due retry may bypass its reservation.
        if (
            row.control == "returning"
            and row.failure == "browser_viewer_revoke_failed"
            and row.control_due_at is not None
            and row.control_due_at > current
            and row.control_due_at != lease
        ):
            return
        if row.claim_until is not None and row.claim_until > current:
            return
        if row.control == "draining" and await lifecycle.unsettled(session, row.id):
            row.failure = "browser_operations_unsettled"
            return
        selected = await lifecycle.target(session, runtime, row)
        assert selected.account is not None
        phase, generation, episode, grant_id = row.control, row.generation, row.episode, row.viewer_id
        assert grant_id is not None and selected.state is not None
        deadline = row.control_expires_at
        baseline = row.human_baseline
        owner = row.controller_thread_id
        row.claim_id = claim
        # Provider open and operation each have a separate budget; close is shielded for 10s.
        row.claim_until = current + timedelta(seconds=2 * runtime.settings.providers.operation_seconds + 25)
    granted: ViewerGrant | None = None
    pages: set[str] = set()
    failure = None
    try:
        async with lifecycle.open_provider(runtime, selected.account) as provider:
            with anyio.fail_after(runtime.settings.providers.operation_seconds):
                if phase in {"draining", "human"}:
                    assert deadline is not None
                    if not selected.account.enabled:
                        raise conflict("browser", browser_id, "browser_provider_disabled")
                    if phase == "draining" and baseline is None:
                        pages = await inventory(provider, selected.state)
                    async with transaction(runtime.storage) as session:
                        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
                        current = await now(session)
                        if not owns_claim(row, claim, generation, episode, phase, current):
                            return
                        if row.control_expires_at is None or row.control_expires_at <= current:
                            await return_control(session, row, current)
                            return
                        if phase == "draining" and baseline is None:
                            row.human_baseline = sorted(pages)
                        deadline = row.control_expires_at
                    granted = await provider.ensure_viewer(selected.state, grant_id, expires_at=deadline)
                else:
                    await provider.revoke_viewer(selected.state, grant_id)
                    if phase == "returning":
                        pages = await inventory(provider, selected.state)
    except Exception:
        failure = "browser_viewer_grant_failed" if phase in {"draining", "human"} else "browser_viewer_revoke_failed"
    async with transaction(runtime.storage) as session:
        await lifecycle.lock_owners(session, (owner,) if owner is not None else ())
        row = await lifecycle.find(session, workspace_id, browser_id, lock=True)
        current = await now(session)
        if not owns_claim(row, claim, generation, episode, phase, current):
            # Clear only our still-current claim, never a replacement claimant's token.
            if row.claim_id == claim and row.claim_until is not None and row.claim_until > current:
                row.claim_id = row.claim_until = None
            return
        row.claim_id = row.claim_until = None
        row.failure = failure
        if phase in {"draining", "human"}:
            if failure is None:
                assert granted is not None and row.control_expires_at is not None
                if row.control_expires_at <= current:
                    failure = "browser_control_expired"
                elif not await controller_allowed(session, runtime, row):
                    failure = "browser_controller_unavailable"
                elif granted.expires_at <= current or granted.expires_at > row.control_expires_at:
                    failure = "browser_viewer_expired"
                elif granted.expires_at < row.control_expires_at and granted.expires_at <= current + renewal_window(
                    runtime
                ):
                    failure = "browser_viewer_lifetime_short"
            if failure is not None:
                row.failure = failure
                await return_control(session, row, current)
                return
            assert granted is not None
            # Never replace a newer grant observation with an older URL/expiry.
            previous = (
                Viewer.model_validate(lifecycle.reveal(runtime, row, "viewer", row.viewer)) if row.viewer else None
            )
            if previous is not None and previous.expires_at > granted.expires_at:
                return
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
            # A keepalive may have advanced the deadline while this supplier call was
            # in flight. Wake maintenance even if another lane rescheduled our lease.
            current_deadline = row.control_expires_at
            if (
                deadline is not None
                and current_deadline is not None
                and deadline < current_deadline
                and granted.expires_at < current_deadline
            ):
                row.control_due_at = current
            return
        if failure is not None:
            row.control_due_at = current + timedelta(seconds=runtime.settings.providers.operation_scan_seconds)
            return
        row.viewer_revoked = True
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
        row.control_expires_at = None
        row.observation_epoch += 1
        row.last_used_at = await now(session)
        for registration in await session.scalars(
            select(BrowserBlockedRow).where(
                BrowserBlockedRow.browser_id == row.id,
                BrowserBlockedRow.generation == row.generation,
                BrowserBlockedRow.episode == episode,
                BrowserBlockedRow.suppressed.is_(False),
            )
        ):
            registration.expires_at = row.last_used_at + timedelta(
                seconds=runtime.settings.control.browser_notice_seconds
            )
            await enqueue_once(
                session,
                organization_id=row.organization_id,
                workspace_id=workspace_id,
                kind="browser_handback",
                dedupe_key=registration.id,
                target={"thread_id": registration.thread_id},
                payload={"registration_id": registration.id},
            )
