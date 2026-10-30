"""Short durable claims around bounded supplier calls, shared by prepare and lazy use."""

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta

import anyio
from a13n_harness import RunConfiguration
from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import JsonValue
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import validate_cdp_endpoint
from a13n_service.providers.browser.definition import BrowserProvider, BrowserProviderError, LiveBrowser
from a13n_service.resources.providers.service import ResolvedProvider, read_provider, resolve_provider
from a13n_service.resources.providers.tables import BrowserProviderRow
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserOperationRow, BrowserRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, authorize


async def find(session: AsyncSession, workspace_id: str, browser_id: str, *, lock: bool = False) -> BrowserRow:
    query = select(BrowserRow).where(BrowserRow.workspace_id == workspace_id, BrowserRow.id == browser_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found("browser", browser_id)
    return row


async def lock_owners(session: AsyncSession, thread_ids: Iterable[str]) -> None:
    """Take ownership FK locks before Browser locks, matching Thread-first control operations."""
    if owners := sorted(set(thread_ids)):
        await session.execute(
            select(ThreadRow.id)
            .where(ThreadRow.id.in_(owners))
            .order_by(ThreadRow.id)
            .with_for_update(read=True, key_share=True)
        )


def usable(row: BrowserRow) -> None:
    if row.status in {"lost", "deleting", "deleted"}:
        raise conflict("browser", row.id, f"browser_{row.status}")


def protect(runtime: Runtime, row: BrowserRow, column: str, value: dict) -> dict:
    return runtime.keys.protect(
        json.dumps(value).encode(), SecretLocation(row.organization_id, "browsers", column, row.id)
    ).model_dump(mode="json")


def reveal(runtime: Runtime, row: BrowserRow, column: str, value: dict | None) -> dict:
    if value is None:
        return {}
    return json.loads(
        runtime.keys.reveal(
            Envelope.model_validate(value), SecretLocation(row.organization_id, "browsers", column, row.id)
        )
    )


def identity(runtime: Runtime, account: ResolvedProvider) -> dict:
    definition = runtime.registry.get("browser", account.type)
    return {
        "type": account.type,
        "backend": definition.backend_identity(definition.configuration_model.model_validate(account.config)),
    }


@asynccontextmanager
async def open_provider(runtime: Runtime, account: ResolvedProvider) -> AsyncIterator[BrowserProvider]:
    definition = runtime.registry.get("browser", account.type)
    with anyio.fail_after(runtime.settings.providers.operation_seconds):
        provider = await definition.open_provider(
            account.config, account.reveal_credential(runtime.keys), runtime.endpoint_policy
        )
    try:
        yield provider
    finally:
        with anyio.CancelScope(shield=True), anyio.move_on_after(10):
            await provider.close()


async def authorize_use(
    session: AsyncSession,
    actor: Principal,
    row: BrowserRow,
    scope: WorkspaceScope,
    *,
    authority: ExecutionAuthority | None = None,
) -> None:
    authorize(actor, scope, "run", authority=authority)
    if row.source == "managed":
        assert row.provider_id is not None
        await resolve_provider(session, actor, BrowserProviderRow, scope, row.provider_id, authority=authority)


def release_intent(row: BrowserRow) -> None:
    row.claim_id = row.claim_until = None
    row.status = "deleted" if row.status == "reserved" else "deleting"
    if row.source == "external_cdp":
        row.status = "deleted"
        row.handle = None
        row.failure = None
        row.observation_epoch += 1


async def connect_external(runtime: Runtime, state: dict, policy: EndpointPolicy) -> Chromium:
    await validate_cdp_endpoint(policy, state["cdp_url"])
    browser = await Chromium.connect(
        state["cdp_url"], headers=state["headers"], timeout=runtime.settings.providers.operation_seconds, discover=False
    )
    try:
        # Browser-only command: reject a page-level debugger socket before exposing native tools.
        await browser.send("Target.getBrowserContexts")
        return browser
    except BaseException:
        with anyio.CancelScope(shield=True), anyio.move_on_after(5):
            await browser.close()
        raise


@dataclass(frozen=True)
class Target:
    browser_id: str
    workspace_id: str
    generation: int
    account: ResolvedProvider | None
    recipe: dict[str, JsonValue]
    state: dict[str, JsonValue] | None = field(repr=False)


async def target(session: AsyncSession, runtime: Runtime, row: BrowserRow) -> Target:
    if row.source == "external_cdp":
        return Target(row.id, row.workspace_id, row.generation, None, {}, reveal(runtime, row, "handle", row.handle))
    assert row.provider_id is not None
    account = await read_provider(session, BrowserProviderRow, row.provider_id)
    if identity(runtime, account) != row.provider_identity:
        raise conflict("browser", row.id, "provider_identity_changed")
    return Target(
        row.id,
        row.workspace_id,
        row.generation,
        account,
        row.recipe,
        reveal(runtime, row, "handle", row.handle) if row.handle else None,
    )


async def unsettled(session: AsyncSession, browser_id: str) -> bool:
    return (
        await session.scalar(
            select(BrowserOperationRow.id)
            .where(BrowserOperationRow.browser_id == browser_id, BrowserOperationRow.settled_at.is_(None))
            .limit(1)
        )
        is not None
    )


async def in_use(
    session: AsyncSession,
    row: BrowserRow,
    *,
    include_unsettled: bool = True,
    include_control: bool = True,
    include_notices: bool = True,
) -> bool:
    if (include_control and row.control != "automation") or (include_unsettled and await unsettled(session, row.id)):
        return True
    if (
        await session.scalar(
            select(RunRow.id)
            .where(
                RunRow.browser_binding["browser_id"].astext == row.id,
                RunRow.status.in_(("accepted", "running", "waiting")),
            )
            .limit(1)
        )
        is not None
    ):
        return True
    if not include_notices:
        return False
    current = await now(session)
    return (
        await session.scalar(
            select(BrowserBlockedRow.id)
            .outerjoin(InboxEntryRow, InboxEntryRow.id == BrowserBlockedRow.entry_id)
            .where(
                BrowserBlockedRow.browser_id == row.id,
                BrowserBlockedRow.generation == row.generation,
                BrowserBlockedRow.episode == row.episode,
                BrowserBlockedRow.suppressed.is_(False),
                or_(BrowserBlockedRow.entry_id.is_(None), InboxEntryRow.status.in_(("pending", "assigned"))),
                BrowserBlockedRow.expires_at > current,
            )
            .limit(1)
        )
        is not None
    )


async def expire(session: AsyncSession, row: BrowserRow) -> bool:
    """End availability at the supplier deadline without pretending uncertain calls settled."""
    if row.status != "ready" or row.expires_at is None or row.expires_at > await now(session):
        return False
    await mark_lost(session, row, "browser_expired")
    return True


async def mark_lost(session: AsyncSession, row: BrowserRow, reason: str) -> None:
    row.status = "lost"
    row.failure = reason
    row.observation_epoch += 1
    row.viewer = None
    await session.execute(
        update(BrowserBlockedRow).where(BrowserBlockedRow.browser_id == row.id).values(suppressed=True)
    )


async def prepare(
    runtime: Runtime,
    workspace_id: str,
    browser_id: str,
    *,
    policy: EndpointPolicy | None = None,
    inspect_ready: bool = False,
) -> None:
    """Start allocation once. Another caller or maintenance can complete the same intent."""
    async with transaction(runtime.storage) as session:
        row = await find(session, workspace_id, browser_id, lock=True)
        usable(row)
        if row.status == "reserved":
            row.status = "creating"
    await reconcile(runtime, workspace_id, browser_id, policy=policy, inspect_ready=inspect_ready)


async def reconcile(
    runtime: Runtime,
    workspace_id: str,
    browser_id: str,
    *,
    inspect_ready: bool = False,
    policy: EndpointPolicy | None = None,
) -> None:
    """Claim one lifecycle step. A timed-out create is inspected, never resubmitted."""
    claim_id = new_object_id("bclaim")
    async with transaction(runtime.storage) as session:
        row = await find(session, workspace_id, browser_id, lock=True)
        current = await now(session)
        phases = {"creating", "deleting", "ready"} if inspect_ready else {"creating", "deleting"}
        if row.status not in phases or (row.claim_until is not None and row.claim_until > current):
            return
        selected = await target(session, runtime, row)
        phase, create = row.status, row.status == "creating" and not row.create_dispatched
        if phase == "creating" and selected.account is not None and not selected.account.enabled:
            row.failure = "browser_provider_disabled"
            return
        row.claim_id = claim_id
        row.claim_until = current + timedelta(seconds=runtime.settings.providers.operation_seconds + 15)
        if phase == "creating":
            row.create_dispatched = True
    live: LiveBrowser | None = None
    failure: str | None = None
    not_dispatched = False
    try:
        if selected.account is None:
            assert selected.state is not None
            if phase != "deleting":
                with anyio.fail_after(runtime.settings.providers.operation_seconds):
                    browser = await connect_external(runtime, selected.state, policy or runtime.endpoint_policy)
                    with anyio.CancelScope(shield=True), anyio.move_on_after(5):
                        await browser.close()
                live = LiveBrowser(selected.state)
        else:
            async with open_provider(runtime, selected.account) as provider:
                with anyio.fail_after(runtime.settings.providers.operation_seconds):
                    if phase == "deleting":
                        await provider.delete(browser_id, selected.state)
                    elif create:
                        recipe = runtime.registry.get("browser", selected.account.type).recipe_model.model_validate(
                            selected.recipe
                        )
                        live = await provider.create(browser_id, recipe)
                    else:
                        live = await provider.inspect(browser_id, selected.state)
                        if live is None and phase == "creating":
                            failure = "browser_create_unresolved"
    except BrowserProviderError as error:
        # Provider codes are internal categories, never upstream exception text.
        failure = "browser_provider_failed"
        not_dispatched = error.certainty == "not_dispatched"
    except Exception:
        failure = "browser_connection_failed" if selected.account is None else "browser_provider_unknown"
    async with transaction(runtime.storage) as session:
        row = await find(session, workspace_id, browser_id, lock=True)
        if row.claim_id != claim_id or row.status != phase:
            return
        row.claim_id = row.claim_until = None
        row.failure = failure
        if failure is not None:
            if phase == "creating" and create and not_dispatched:
                row.create_dispatched = False
            return
        if phase == "deleting":
            row.status = "deleted"
            row.handle = row.viewer = row.human_baseline = None
            row.viewer_id = row.controller_id = row.controller_thread_id = None
            row.control = "automation"
            row.control_expires_at = None
        elif phase == "ready" and live is None:
            await mark_lost(session, row, "browser_lost")
        else:
            assert live is not None
            row.handle = protect(runtime, row, "handle", live.state)
            row.expires_at = live.expires_at
            row.status = "ready"
            if phase == "creating":
                row.last_used_at = await now(session)
            await expire(session, row)


async def ready_target(
    runtime: Runtime, workspace_id: str, browser_id: str, *, configuration: RunConfiguration | None = None
) -> Target:
    await prepare(
        runtime, workspace_id, browser_id, policy=runtime.endpoint_policy.for_run(configuration or RunConfiguration())
    )
    async with short_session(runtime.storage) as session:
        row = await find(session, workspace_id, browser_id)
        usable(row)
        if row.status != "ready":
            raise conflict("browser", browser_id, "browser_preparing")
        if row.expires_at is not None and row.expires_at <= await now(session):
            raise conflict("browser", browser_id, "browser_expired")
        selected = await target(session, runtime, row)
        if selected.account is not None and not selected.account.enabled:
            raise conflict("browser", browser_id, "browser_provider_disabled")
        return selected
