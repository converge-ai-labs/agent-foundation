"""Native-desktop control intent and renewable guest input grants."""

import json
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import enqueue_once
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.runs.environments import service
from a13n_service.runs.environments.desktop import access, identity
from a13n_service.runs.environments.desktop.schemas import Control, Renew, Takeover, Viewer, viewer_url
from a13n_service.runs.environments.desktop.tables import ComputerOperationRow, DesktopBlockedRow, DesktopControlRow
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ThreadRow
from a13n_service.runs.threads import get_thread, require_open
from a13n_service.tenancy.access import principal_for, workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, authorize


def lease_seconds(runtime: Runtime, requested: int | None) -> int:
    policy = runtime.settings.control
    seconds = policy.desktop_control_seconds if requested is None else requested
    if not policy.desktop_control_min_seconds <= seconds <= policy.desktop_control_max_seconds:
        raise invalid(
            "lease_seconds",
            f"must be between {policy.desktop_control_min_seconds} and {policy.desktop_control_max_seconds}",
        )
    return seconds


async def find(session: AsyncSession, runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str):
    scope = await workspace_scope(session, actor, workspace_id, "run")
    environment = await service._find(session, scope, environment_id, lock=True)
    selected = await access.target(session, runtime, environment, actor=actor)
    key = identity.environment_key(environment)
    assert key is not None
    return environment, await identity.target(session, key), selected


def audit(
    session: AsyncSession, actor: Principal, environment: EnvironmentRow, row: DesktopControlRow, action: str
) -> None:
    record(
        session,
        WorkspaceScope(environment.organization_id, environment.workspace_id),
        actor_id=actor.id,
        action="desktop_control." + action,
        target_kind="desktop_control",
        target_id=row.id,
        details={"environment_id": environment.id, "generation": row.generation, "episode": row.episode},
    )


def protect(runtime: Runtime, environment: EnvironmentRow, row: DesktopControlRow, viewer: Viewer) -> dict:
    return runtime.keys.protect(
        viewer.model_dump_json().encode(),
        SecretLocation(environment.organization_id, "desktop_controls", "viewer", row.id),
    ).model_dump(mode="json")


def reveal(runtime: Runtime, environment: EnvironmentRow, row: DesktopControlRow) -> Viewer | None:
    if row.viewer is None:
        return None
    return Viewer.model_validate(
        json.loads(
            runtime.keys.reveal(
                Envelope.model_validate(row.viewer),
                SecretLocation(environment.organization_id, "desktop_controls", "viewer", row.id),
            )
        )
    )


async def get(runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str) -> Control:
    async with transaction(runtime.storage) as session:
        _, row, _ = await find(session, runtime, actor, workspace_id, environment_id)
        return Control.model_validate(row)


async def takeover(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, body: Takeover, *, if_match: str | None
) -> Control:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, workspace_id, body.thread_id, lock=True)
        require_open(thread)
        binding = await session.scalar(
            select(ThreadEnvironmentRow).where(
                ThreadEnvironmentRow.thread_id == thread.id, ThreadEnvironmentRow.environment_id == environment_id
            )
        )
        if binding is None:
            raise conflict("thread", thread.id, "environment_binding_missing")
        environment, row, _ = await find(session, runtime, actor, workspace_id, environment_id)
        require_match(if_match, row.id, row.version)
        if row.control != "automation":
            raise conflict("environment", environment_id, "desktop_control_pending")
        row.episode += 1
        row.control = "draining"
        row.environment_id, row.provider_identity = environment.id, environment.provider_identity
        row.controller_id, row.controller_thread_id = actor.id, thread.id
        row.expires_at = await now(session) + timedelta(seconds=lease_seconds(runtime, body.lease_seconds))
        row.grant_id = new_object_id("dgrant")
        row.grant_dispatched = False
        row.guest_generation = row.viewer = row.failure = None
        row.claim_id = row.claim_until = row.due_at = None
        await session.execute(
            update(DesktopBlockedRow).where(DesktopBlockedRow.control_id == row.id).values(suppressed=True)
        )
        audit(session, actor, environment, row, "takeover")
        control_id = row.id
    await reconcile(runtime, control_id)
    return await get(runtime, actor, workspace_id, environment_id)


async def return_control(session: AsyncSession, row: DesktopControlRow) -> None:
    if row.control == "draining" and not row.grant_dispatched:
        # The grant-intent transaction must validate phase and claim before dispatch.
        row.control = "automation"
        row.expires_at = row.viewer = row.grant_id = None
        row.controller_id = row.controller_thread_id = None
        row.claim_id = row.claim_until = row.due_at = None
        await session.execute(
            update(DesktopBlockedRow).where(DesktopBlockedRow.control_id == row.id).values(suppressed=True)
        )
    elif row.control != "automation":
        if row.control != "returning":
            row.due_at = None
        row.control = "returning"
        row.viewer = None


async def handback(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, *, if_match: str | None
) -> Control:
    async with transaction(runtime.storage) as session:
        environment, row, _ = await find(session, runtime, actor, workspace_id, environment_id)
        require_match(if_match, row.id, row.version)
        await return_control(session, row)
        audit(session, actor, environment, row, "handback")
        control_id = row.id
    await reconcile(runtime, control_id)
    return await get(runtime, actor, workspace_id, environment_id)


async def controller_allowed(
    session: AsyncSession, runtime: Runtime, environment: EnvironmentRow, row: DesktopControlRow
) -> bool:
    if row.controller_id is None or row.controller_thread_id is None:
        return False
    thread = await session.get(ThreadRow, row.controller_thread_id)
    if thread is None or thread.archived_at is not None:
        return False
    binding = await session.scalar(
        select(ThreadEnvironmentRow).where(
            ThreadEnvironmentRow.thread_id == thread.id, ThreadEnvironmentRow.environment_id == environment.id
        )
    )
    if (
        binding is None
        or identity.environment_key(environment) != row.target_key
        or environment.provider_identity != row.provider_identity
    ):
        return False
    scope = WorkspaceScope(environment.organization_id, environment.workspace_id)
    try:
        actor = await principal_for(session, runtime.access, row.controller_id, confinement=scope)
        authorize(actor, scope, "run")
        await access.target(session, runtime, environment, actor=actor)
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        return False
    return True


async def renew(runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, body: Renew) -> Control:
    async with transaction(runtime.storage) as session:
        _, row, _ = await find(session, runtime, actor, workspace_id, environment_id)
        current = await now(session)
        if (row.generation, row.episode, row.controller_id) != (
            body.generation,
            body.episode,
            actor.id,
        ) or row.control not in {"draining", "human"}:
            raise conflict("environment", environment_id, "desktop_control_changed")
        if row.expires_at is None or row.expires_at <= current:
            raise conflict("environment", environment_id, "desktop_control_expired")
        environment = await session.get(EnvironmentRow, row.environment_id)
        if environment is None or not await controller_allowed(session, runtime, environment, row):
            raise conflict("environment", environment_id, "desktop_controller_unavailable")
        previous = reveal(runtime, environment, row)
        if row.control == "human" and (previous is None or previous.expires_at <= current):
            raise conflict("environment", environment_id, "desktop_viewer_expired")
        row.expires_at = max(row.expires_at, current + timedelta(seconds=lease_seconds(runtime, body.lease_seconds)))
        await session.flush()
        return Control.model_validate(row)


async def locked(session: AsyncSession, control_id: str) -> tuple[EnvironmentRow, DesktopControlRow]:
    # Environment -> target is also the Computer admission order.
    snapshot = await session.get(DesktopControlRow, control_id)
    if snapshot is None or snapshot.environment_id is None:
        raise not_found("desktop_control", control_id)
    environment = await lock(session, EnvironmentRow, snapshot.environment_id)
    row = await lock(session, DesktopControlRow, control_id)
    assert environment is not None and row is not None
    if row.environment_id != environment.id:
        raise conflict("desktop_control", control_id, "desktop_control_changed")
    return environment, row


def owns_claim(
    row: DesktopControlRow, claim: str, generation: int, episode: int, phase: str, current: datetime
) -> bool:
    return (
        row.claim_id == claim
        and row.claim_until is not None
        and row.claim_until > current
        and (row.generation, row.episode, row.control) == (generation, episode, phase)
    )


async def reopen(session: AsyncSession, runtime: Runtime, row: DesktopControlRow, current: datetime) -> None:
    row.control = "automation"
    row.expires_at = row.viewer = row.grant_id = None
    row.controller_id = row.controller_thread_id = None
    row.grant_dispatched = False
    row.due_at = row.failure = None
    for registration in await session.scalars(
        select(DesktopBlockedRow).where(
            DesktopBlockedRow.control_id == row.id,
            DesktopBlockedRow.generation == row.generation,
            DesktopBlockedRow.episode == row.episode,
            DesktopBlockedRow.suppressed.is_(False),
        )
    ):
        thread = await session.get(ThreadRow, registration.thread_id)
        assert thread is not None
        registration.expires_at = current + timedelta(seconds=runtime.settings.control.desktop_notice_seconds)
        await enqueue_once(
            session,
            organization_id=thread.organization_id,
            workspace_id=thread.workspace_id,
            kind="desktop_handback",
            dedupe_key=registration.id,
            target={"thread_id": thread.id},
            payload={"registration_id": registration.id},
        )


async def reconcile(runtime: Runtime, control_id: str) -> None:
    claim = new_object_id("dclaim")
    async with transaction(runtime.storage) as session:
        environment, row = await locked(session, control_id)
        current = await now(session)
        if row.control == "automation":
            return
        if row.control in {"draining", "human"}:
            previous = reveal(runtime, environment, row)
            reason = None
            if row.expires_at is None or row.expires_at <= current:
                reason = "desktop_control_expired"
            elif not await controller_allowed(session, runtime, environment, row):
                reason = "desktop_controller_unavailable"
            elif row.control == "human" and (previous is None or previous.expires_at <= current):
                reason = "desktop_viewer_expired"
            if reason:
                row.failure = reason
                await return_control(session, row)
        if row.control == "automation" or (row.claim_until is not None and row.claim_until > current):
            return
        if row.due_at is not None and row.due_at > current:
            return
        if row.control == "draining" and await session.scalar(
            select(exists().where(ComputerOperationRow.control_id == row.id, ComputerOperationRow.settled_at.is_(None)))
        ):
            row.failure = "desktop_operations_unsettled"
            row.due_at = current + timedelta(seconds=access.SCAN_SECONDS)
            return
        try:
            selected = await access.target(session, runtime, environment)
            if (
                identity.environment_key(environment) != row.target_key
                or environment.provider_identity != row.provider_identity
            ):
                raise conflict("environment", environment.id, "desktop_target_changed")
        except ServiceError:
            row.failure = "desktop_target_unavailable"
            await return_control(session, row)
            row.due_at = current + timedelta(seconds=access.RENEW_SECONDS)
            return
        phase, generation, episode = row.control, row.generation, row.episode
        grant_id, guest_generation, deadline = row.grant_id, row.guest_generation, row.expires_at
        assert grant_id is not None
        row.claim_id, row.claim_until = claim, current + timedelta(seconds=access.CLAIM_SECONDS)
    granted = None
    failure = None
    try:
        async with access.connection(runtime, selected, claim) as send:
            description = await send(wire.Describe())
            if not isinstance(description, wire.Description) or not description.revocation_fences_grants:
                raise ValueError("Desktop grant fencing is unavailable")
            if guest_generation is not None and description.generation != guest_generation:
                raise ValueError("Desktop guest generation changed")
            guest_generation = description.generation
            if phase in {"draining", "human"}:
                route = viewer_url(description.websocket_url)
                async with transaction(runtime.storage) as session:
                    environment, row = await locked(session, control_id)
                    current = await now(session)
                    if not owns_claim(row, claim, generation, episode, phase, current):
                        return
                    if (
                        row.expires_at is None
                        or row.expires_at <= current
                        or not await controller_allowed(session, runtime, environment, row)
                    ):
                        await return_control(session, row)
                        return
                    deadline = row.expires_at
                    row.guest_generation = guest_generation
                    # Durable possible dispatch survives cancellation, timeout and worker death.
                    row.grant_dispatched = True
                assert deadline is not None
                reply = await send(
                    wire.Grant(
                        id=grant_id,
                        generation=guest_generation,
                        epoch=episode,
                        mode="control",
                        expires_at=deadline.timestamp(),
                    )
                )
                if (
                    not isinstance(reply, wire.AccessGrant)
                    or reply.state != "active"
                    or reply.token is None
                    or reply.password is None
                ):
                    raise ValueError("Desktop grant is not active")
                granted = Viewer(
                    mode="control",
                    websocket_url=route + "?token=" + quote(reply.token.get_secret_value(), safe=""),
                    password=reply.password.get_secret_value(),
                    expires_at=min(deadline, current + timedelta(seconds=reply.remaining_seconds)),
                    generation=generation,
                    episode=episode,
                )
            else:
                assert guest_generation is not None
                revoked = await send(
                    wire.Inspect(operation="revoke", id=grant_id, generation=guest_generation, epoch=episode)
                )
                if not isinstance(revoked, wire.AccessGrant) or revoked.state not in {"revoked", "expired", "failed"}:
                    raise ValueError("Desktop revocation is unconfirmed")
                settled = await send(wire.Settle(generation=guest_generation))
                if not isinstance(settled, wire.Settlement):
                    raise ValueError("Desktop input cleanup is unconfirmed")
    except Exception:
        failure = "desktop_grant_failed" if phase in {"draining", "human"} else "desktop_revoke_failed"
    async with transaction(runtime.storage) as session:
        environment, row = await locked(session, control_id)
        current = await now(session)
        if not owns_claim(row, claim, generation, episode, phase, current):
            if row.claim_id == claim:
                row.claim_id = row.claim_until = None
            return
        row.claim_id = row.claim_until = None
        if phase in {"draining", "human"}:
            if failure is None and (
                granted is None
                or granted.expires_at <= current
                or row.expires_at is None
                or row.expires_at <= current
                or not await controller_allowed(session, runtime, environment, row)
            ):
                failure = "desktop_control_unavailable"
            if failure is not None:
                row.failure = failure
                await return_control(session, row)
                return
            assert granted is not None
            row.viewer = protect(runtime, environment, row, granted)
            row.control, row.failure = "human", None
            row.due_at = min(granted.expires_at, current + timedelta(seconds=access.RENEW_SECONDS))
        elif failure is not None:
            row.failure = failure
            row.due_at = current + timedelta(seconds=access.RENEW_SECONDS)
        else:
            await reopen(session, runtime, row, current)
