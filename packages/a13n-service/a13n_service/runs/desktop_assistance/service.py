"""Durable Agent-requested assistance. Provider I/O always runs outside storage scopes."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Literal
from urllib.parse import quote

import anyio
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.providers.environment.management import DesktopSessionProvider
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import Lease, execution_principals, lock_lease
from a13n_service.runs.desktop_assistance.admission import aliases, environment_key, target
from a13n_service.runs.desktop_assistance.schemas import Access, Completion, Control, Request, View, viewer_url
from a13n_service.runs.desktop_assistance.tables import (
    DesktopAssistanceRow,
    DesktopExecutionRow,
    DesktopTargetRow,
    DesktopViewerRow,
)
from a13n_service.runs.environments.adapters import Target, close, open_provider, provider_identity
from a13n_service.runs.environments.mounts import require_usable
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Pending, Resume, Returned
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.tool_executions.authority import lock_owner
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

# Access performs discovery, optional renewal and grant as sequential remote calls.
# Bound the entire request before the durable claim can be reclaimed by a sweep.
_ACCESS_TIMEOUT_SECONDS = 90
_ACCESS_CLEANUP_SECONDS = 15

TOOL = "request_desktop_assistance"
LIVE = ("preparing", "awaiting_user", "claiming", "human_control", "revoking", "ready_to_resume", "blocked", "consumed")


async def provider_target(
    session: AsyncSession, runtime: Runtime, actor: Principal, environment: EnvironmentRow
) -> Target:
    require_usable(environment, actor.id)
    if environment.status != "ready" or environment.provider_id is None or environment.handle is None:
        raise conflict("environment", environment.id, "desktop_not_ready")
    handle = Handle.model_validate(environment.handle)
    if not handle.recipe.get("desktop") or not handle.recipe.get("desktop_assistance"):
        raise conflict("environment", environment.id, "desktop_assistance_not_enabled")
    provider = await resolve_provider(
        session,
        actor,
        EnvironmentProviderRow,
        WorkspaceScope(environment.organization_id, environment.workspace_id),
        environment.provider_id,
    )
    if environment.provider_identity != provider_identity(runtime.registry, provider):
        raise conflict("environment", environment.id, "provider_identity_changed")
    if provider.type not in {"docker", "e2b", "daytona", "modal", "vercel", "sprites"}:
        raise conflict("environment", environment.id, "desktop_assistance_unsupported")
    if handle.recipe.get("init_script"):
        raise conflict("environment", environment.id, "desktop_background_work_unconfirmed")
    return Target(environment.id, provider, handle.recipe, handle.state)


async def audit_transition(session: AsyncSession, actor: Principal, row: DesktopAssistanceRow, action: str) -> None:
    environment = await session.get(EnvironmentRow, row.environment_id)
    assert environment is not None
    record(
        session,
        WorkspaceScope(environment.organization_id, environment.workspace_id),
        actor_id=actor.id,
        action="desktop_assistance." + action,
        target_kind="desktop_assistance",
        target_id=row.id,
        details={"run_id": row.run_id, "environment_id": row.environment_id, "epoch": row.epoch, "state": row.state},
    )


async def dispatch(runtime: Runtime, target: Target, request: wire.Request, *, operation_id: str) -> wire.Reply:
    provider = await open_provider(runtime, target)
    try:
        return await provider.desktop_access(
            dict(target.recipe),
            environment_id=target.environment_id,
            state=target.state,
            operation_id=operation_id,
            request=request,
        )
    finally:
        await close(provider)


@asynccontextmanager
async def access_dispatch(
    runtime: Runtime, target: Target, *, operation_id: str
) -> AsyncGenerator[Callable[[wire.Request], Awaitable[wire.Reply]]]:
    if target.state is None or target.state.provider_key != "modal":
        yield partial(dispatch, runtime, target, operation_id=operation_id)
        return
    provider = await open_provider(runtime, target)
    try:
        if not isinstance(provider, DesktopSessionProvider):
            raise ValueError("Modal desktop access requires its scoped provider")
        async with provider.desktop_session(
            dict(target.recipe), environment_id=target.environment_id, state=target.state, operation_id=operation_id
        ) as management:
            yield management.desktop_access
    finally:
        await close(provider)


async def verify_continuation(runtime: Runtime, lease: Lease, selected: Target) -> None:
    from a13n_service.runs.desktop_assistance.admission import target_key

    key = target_key(selected.state)
    if key is None:
        return
    async with transaction(runtime.storage) as session:
        await lock_owner(session, runtime, lease)
        control = await lock(session, DesktopTargetRow, key)
        row = await session.get(DesktopAssistanceRow, control.holder_id) if control and control.holder_id else None
        if row is None or row.state != "consumed" or row.successor_id != lease.run_id:
            return
        generation = row.generation
    async with access_dispatch(runtime, selected, operation_id=new_object_id("dtop")) as send:
        description = await send(wire.Describe())
        valid_generation = False
        if (
            generation is not None
            and isinstance(description, wire.Description)
            and description.generation == generation
        ):
            valid_generation = True
            await send(wire.Settle(generation=generation))
    # Raise outside the provider scope to preserve the service conflict after cleanup.
    if not valid_generation:
        raise conflict("environment", selected.environment_id, "desktop_generation_changed")


async def require_no_agent_request(session: AsyncSession, run_id: str) -> None:
    """The Run lock serializes both origins, even when they select different mounts."""
    if await session.scalar(
        select(exists().where(DesktopAssistanceRow.run_id == run_id, DesktopAssistanceRow.cause == "agent"))
    ):
        raise conflict("run", run_id, "desktop_agent_assistance_pending")


async def request_assistance(
    runtime: Runtime, lease: Lease, actor: Principal, call_id: str, body: Request
) -> str | None:
    from a13n_service.runs.desktop_assistance.execution import request as user_request

    async with transaction(runtime.storage) as session:
        run, _, _ = await lock_lease(session, lease)
        if await user_request(session, run.id) is not None:
            return None
        frozen = next((mount for mount in run.environment_mounts if mount["name"] == body.mount), None)
        if frozen is None:
            raise not_found("mount", body.mount)
        if "environment.computer.observe" not in frozen.get("permission_ceiling", {}).get("operations", []):
            raise conflict("mount", body.mount, "desktop_observation_not_permitted")
        environment = await lock(session, EnvironmentRow, frozen["environment_id"])
        assert environment is not None
        selected = await provider_target(session, runtime, actor, environment)
        key = environment_key(environment)
        if key is None:
            raise conflict("environment", environment.id, "desktop_identity_unavailable")
    description = await dispatch(runtime, selected, wire.Describe(), operation_id=new_object_id("dtop"))
    if not isinstance(description, wire.Description) or not description.revocation_fences_grants:
        raise conflict("environment", selected.environment_id, "desktop_endpoint_unavailable")
    try:
        viewer_url(description.websocket_url)
    except ValueError as error:
        raise conflict("environment", selected.environment_id, "desktop_route_unavailable") from error
    async with transaction(runtime.storage) as session:
        run, _, current = await lock_lease(session, lease)
        # Discovery is outside the transaction: user takeover may win while it runs.
        # No assistance/grant was created, so this is a known no-op, not uncertain work.
        if await user_request(session, run.id) is not None:
            return None
        environment = await lock(session, EnvironmentRow, selected.environment_id)
        if environment is None or environment_key(environment) != key:
            raise conflict("environment", selected.environment_id, "desktop_target_changed")
        await provider_target(session, runtime, actor, environment)
        control = await target(session, key, environment_id=environment.id, run_id=run.id)
        previous = await session.scalar(
            select(DesktopAssistanceRow).where(
                DesktopAssistanceRow.run_id == run.id, DesktopAssistanceRow.tool_call_id == call_id
            )
        )
        if previous is not None:
            if previous.mount != body.mount or previous.reason != body.reason:
                raise conflict("desktop_assistance", previous.id, "request_changed")
            return previous.id
        if control.holder_id is not None or control.unmanaged_input:
            raise conflict("environment", environment.id, "desktop_busy_or_background_work_unconfirmed")
        # Admission takes the same target lock before committing another Run's mounts.
        identities = await aliases(session, environment)
        if await session.scalar(
            select(
                exists().where(
                    RunRow.id != run.id,
                    RunRow.status.in_(("accepted", "running")),
                    or_(
                        *(RunRow.environment_mounts.contains([{"environment_id": identity}]) for identity in identities)
                    ),
                )
            )
        ):
            raise conflict("environment", environment.id, "desktop_busy")
        identity = new_object_id("dassist")
        control.holder_id = identity
        session.add(
            DesktopAssistanceRow(
                id=identity,
                workspace_id=run.workspace_id,
                run_id=run.id,
                tool_call_id=call_id,
                environment_id=environment.id,
                target_key=key,
                mount=body.mount,
                reason=body.reason,
                state="preparing",
                generation=description.generation,
                deadline=current + timedelta(minutes=15),
            )
        )
        return identity


async def sealed(session: AsyncSession, run: RunRow, pending: Pending | None) -> None:
    rows = (await session.scalars(select(DesktopAssistanceRow).where(DesktopAssistanceRow.run_id == run.id))).all()
    calls = {call.tool_call_id for call in pending.calls if call.tool_name == TOOL} if pending else set()
    for assistance in sorted(rows, key=lambda item: item.target_key):
        control = await lock(session, DesktopTargetRow, assistance.target_key)
        assert control is not None
        if assistance.state != "preparing":
            continue
        if (
            assistance.cause == "user"
            and run.status == "completed"
            and not control.inflight
            and not control.unmanaged_input
        ):
            assistance.state = "awaiting_user"
            continue
        matches = (
            pending is not None and pending.desktop_control_id == assistance.id
            if assistance.cause == "user"
            else assistance.tool_call_id in calls
        )
        if not matches or control.inflight or control.unmanaged_input:
            assistance.state, assistance.failure = "blocked", "desktop_execution_unconfirmed"
        else:
            assistance.state = "awaiting_user"


async def validate_resume(session: AsyncSession, run: RunRow, answers: Resume) -> list[DesktopAssistanceRow]:
    pending = Pending.model_validate(run.pending)
    rows = (
        await session.scalars(
            select(DesktopAssistanceRow).where(
                DesktopAssistanceRow.run_id == run.id,
                or_(DesktopAssistanceRow.cause == "agent", DesktopAssistanceRow.id == pending.desktop_control_id),
            )
        )
    ).all()
    if pending.desktop_control_id is not None:
        pause = next((row for row in rows if row.id == pending.desktop_control_id), None)
        if pause is None or pause.state not in {"ready_to_resume", "abandoned"}:
            raise conflict("run", run.id, "desktop_assistance_not_completed")
    by_call = {row.tool_call_id: row for row in rows}
    for call in pending.calls:
        if call.tool_name != TOOL:
            continue
        row = by_call.get(call.tool_call_id)
        answer = answers.calls[call.tool_call_id]
        if (
            row is None
            or row.state not in {"ready_to_resume", "abandoned"}
            or not isinstance(answer, Returned)
            or answer.value != row.outcome
        ):
            raise conflict("run", run.id, "desktop_assistance_not_completed")
    for row in sorted(rows, key=lambda item: item.environment_id):
        environment = await lock(session, EnvironmentRow, row.environment_id)
        if environment is None or environment.status != "ready" or environment_key(environment) != row.target_key:
            raise conflict("environment", row.environment_id, "desktop_target_changed")
        control = await lock(session, DesktopTargetRow, row.target_key)
        if control is not None and row.state == "abandoned" and control.holder_id is None:
            identities = await aliases(session, environment)
            if await session.scalar(
                select(
                    exists().where(
                        RunRow.status.in_(("accepted", "running")),
                        or_(
                            *(
                                RunRow.environment_mounts.contains([{"environment_id": identity}])
                                for identity in identities
                            )
                        ),
                    )
                )
            ):
                raise conflict("environment", environment.id, "desktop_busy")
            control.holder_id = row.id
        if control is None or control.holder_id != row.id or control.inflight or control.unmanaged_input:
            raise conflict("run", run.id, "desktop_authority_unconfirmed")
    return list(rows)


async def authorized(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    identity: str,
    *,
    waiting: bool = True,
    disconnecting: bool = False,
) -> tuple[DesktopAssistanceRow, DesktopTargetRow, Target]:
    await workspace_scope(session, actor, workspace_id, "run")
    initial = await session.get(DesktopAssistanceRow, identity)
    if initial is None or initial.workspace_id != workspace_id:
        raise not_found("desktop_assistance", identity)
    run = await session.get(RunRow, initial.run_id)
    assert run is not None
    thread = await lock(session, ThreadRow, run.thread_id)
    assert thread is not None
    await session.refresh(run)
    await lock(session, RunRow, run.id)
    if initial.cause == "user" and not disconnecting:
        await require_no_agent_request(session, run.id)
    if (
        waiting
        and initial.cause == "agent"
        and (
            run.status != "waiting"
            or thread.last_run_id != run.id
            or thread.current_run_id is not None
            or thread.archived_at is not None
        )
    ):
        raise conflict("run", run.id, "not_idle_waiting_head")
    if initial.cause == "user" and (
        thread.archived_at is not None or run.id not in {thread.current_run_id, thread.last_run_id}
    ):
        raise conflict("run", run.id, "desktop_run_superseded")
    principals = await execution_principals(session, runtime.access, [run])
    if isinstance(principals.get(run.id), ServiceError):
        raise ServiceError("forbidden", "The waiting run no longer has execution authority")
    environment = await lock(session, EnvironmentRow, initial.environment_id)
    if environment is None or environment_key(environment) != initial.target_key:
        raise conflict("environment", initial.environment_id, "desktop_target_changed")
    selected = await provider_target(session, runtime, actor, environment)
    control = await lock(session, DesktopTargetRow, initial.target_key)
    assert control is not None
    row = await lock(session, DesktopAssistanceRow, identity)
    assert row is not None
    if control.holder_id != row.id and not (
        row.cause == "user"
        and ((row.state == "viewing" and control.holder_id is None) or (disconnecting and row.controller_id is None))
    ):
        raise conflict("desktop_assistance", identity, "desktop_reservation_lost")
    return row, control, selected


async def read(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, call_id: str) -> View:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        row = await session.scalar(
            select(DesktopAssistanceRow).where(
                DesktopAssistanceRow.workspace_id == workspace_id,
                DesktopAssistanceRow.run_id == run_id,
                DesktopAssistanceRow.tool_call_id == call_id,
            )
        )
        if row is None:
            raise not_found("desktop_assistance", call_id)
        return View.model_validate(row)


def available(row: DesktopAssistanceRow, current) -> None:
    if row.deadline <= current:
        raise conflict("desktop_assistance", row.id, "desktop_assistance_expired")
    if row.state in {"preparing", "revoking", "blocked", "consumed", "abandoned", "ready_to_resume"}:
        raise conflict("desktop_assistance", row.id, "desktop_assistance_not_ready")


@dataclass(frozen=True)
class AccessClaim:
    identity: str
    operation_id: str
    selected: Target
    generation: str
    grant_id: str
    epoch: int
    ttl: int
    mode: Literal["view", "control"]
    renew: bool


async def claim_access(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    identity: str,
    body: Control,
    *,
    mode: Literal["view", "control"],
    renew: bool,
    operation_id: str,
) -> AccessClaim:
    async with transaction(runtime.storage) as session:
        row, control, selected = await authorized(session, runtime, actor, workspace_id, identity)
        current = await now(session)
        available(row, current)
        if mode == "control" and row.state == "viewing":
            raise conflict("desktop_assistance", identity, "desktop_pause_required")
        if row.operation_expires_at is not None and row.operation_expires_at > current:
            raise conflict("desktop_assistance", identity, "desktop_transition_pending")
        if mode == "control" and (control.inflight or control.unmanaged_input):
            raise conflict("desktop_assistance", identity, "desktop_execution_unconfirmed")
        assert row.generation is not None
        generation = row.generation
        ttl = min(60, int((row.deadline - current).total_seconds()))
        if ttl < 1:
            raise conflict("desktop_assistance", identity, "desktop_assistance_expired")
        if mode == "control":
            execution = await session.get(DesktopExecutionRow, row.run_id)
            if row.cause == "user" and (execution is None or execution.inflight or execution.uncertain):
                raise conflict("run", row.run_id, "desktop_execution_unconfirmed")
            run = await session.get(RunRow, row.run_id)
            if row.cause == "user" and (run is None or run.status not in {"waiting", "completed"}):
                raise conflict("run", row.run_id, "desktop_pause_required")
            if row.controller_id is not None and (row.controller_id, row.controller_session) != (
                actor.id,
                body.session_id,
            ):
                raise conflict("desktop_assistance", identity, "desktop_controller_exists")
            if row.controller_id is None:
                if body.epoch != row.epoch or renew:
                    raise conflict("desktop_assistance", identity, "desktop_stale_epoch")
                control.epoch += 1
                row.epoch = control.epoch
                row.controller_id, row.controller_session = actor.id, body.session_id
                row.grant_id = new_object_id("dgrant")
            elif body.epoch not in {row.epoch, row.epoch - 1} or (renew and body.epoch != row.epoch):
                raise conflict("desktop_assistance", identity, "desktop_stale_epoch")
            row.state = "claiming"
            grant_id, epoch = row.grant_id, row.epoch
        else:
            viewer = await session.scalar(
                select(DesktopViewerRow).where(
                    DesktopViewerRow.assistance_id == row.id,
                    DesktopViewerRow.principal_id == actor.id,
                    DesktopViewerRow.session_id == body.session_id,
                    DesktopViewerRow.revoked.is_(False),
                    DesktopViewerRow.expires_at > current,
                )
            )
            if viewer is None:
                if renew:
                    raise conflict("desktop_assistance", identity, "desktop_grant_expired")
                viewer = DesktopViewerRow(
                    id=new_object_id("dgrant"),
                    assistance_id=row.id,
                    principal_id=actor.id,
                    session_id=body.session_id,
                    generation=generation,
                    epoch=max(1, row.epoch),
                    expires_at=current + timedelta(seconds=ttl),
                )
                session.add(viewer)
            grant_id, epoch = viewer.id, viewer.epoch
        assert grant_id is not None
        row.operation_id, row.operation_expires_at = (
            operation_id,
            current + timedelta(seconds=_ACCESS_TIMEOUT_SECONDS + _ACCESS_CLEANUP_SECONDS),
        )
    return AccessClaim(identity, operation_id, selected, generation, grant_id, epoch, ttl, mode, renew)


async def grant_access(
    claim: AccessClaim,
    description: wire.Reply,
    send: Callable[[wire.Request], Awaitable[wire.Reply]],
) -> tuple[wire.AccessGrant, str]:
    if not isinstance(description, wire.Description) or description.generation != claim.generation:
        raise ValueError("Desktop generation changed")
    route = viewer_url(description.websocket_url)
    if claim.renew:
        await send(wire.Renew(generation=claim.generation, id=claim.grant_id, epoch=claim.epoch, ttl_seconds=claim.ttl))
    result = await send(
        wire.Grant(
            generation=claim.generation,
            id=claim.grant_id,
            mode=claim.mode,
            epoch=claim.epoch,
            ttl_seconds=claim.ttl,
        )
    )
    if (
        not isinstance(result, wire.AccessGrant)
        or result.state != "active"
        or result.token is None
        or result.password is None
    ):
        raise ValueError("Desktop grant is no longer active")
    return result, route


async def publish_access(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    claim: AccessClaim,
    result: wire.AccessGrant,
    route: str,
) -> Access:
    assert result.token is not None and result.password is not None
    async with transaction(runtime.storage) as session:
        row, _, _ = await authorized(session, runtime, actor, workspace_id, claim.identity)
        current = await now(session)
        if (
            row.operation_id != claim.operation_id
            or row.operation_expires_at is None
            or row.operation_expires_at <= current
            or row.deadline <= current
        ):
            raise conflict("desktop_assistance", claim.identity, "desktop_transition_superseded")
        if claim.mode == "control":
            row.state = "human_control"
            row.lease_expires_at = current + timedelta(seconds=result.remaining_seconds)
        else:
            viewer = await session.get(DesktopViewerRow, claim.grant_id)
            assert viewer is not None
            viewer.expires_at = current + timedelta(seconds=result.remaining_seconds)
        row.operation_id, row.operation_expires_at = None, None
        if not claim.renew:
            await audit_transition(session, actor, row, claim.mode)
        return Access(
            assistance=View.model_validate(row),
            mode=claim.mode,
            websocket_url=route + "?token=" + quote(result.token.get_secret_value(), safe=""),
            password=result.password.get_secret_value(),
            expires_in_seconds=result.remaining_seconds,
        )


async def access(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    identity: str,
    body: Control,
    *,
    mode: Literal["view", "control"],
    renew: bool = False,
) -> Access:
    operation_id = new_object_id("dtop")
    claim = await claim_access(
        runtime, actor, workspace_id, identity, body, mode=mode, renew=renew, operation_id=operation_id
    )
    try:
        with anyio.fail_after(_ACCESS_TIMEOUT_SECONDS):
            async with access_dispatch(runtime, claim.selected, operation_id=operation_id) as send:
                result, route = await grant_access(claim, await send(wire.Describe()), send)
            return await publish_access(runtime, actor, workspace_id, claim, result, route)
    except BaseException as error:
        # Never claim rollback of a dispatched grant. Its bounded lease remains the fallback.
        await blocked(runtime, identity, operation_id)
        if isinstance(error, Exception) and not isinstance(error, ServiceError):
            raise ServiceError(
                "unavailable", "Desktop transition could not be confirmed", {"dependency": "desktop_access"}
            ) from error
        raise


async def blocked(runtime: Runtime, identity: str, operation_id: str) -> None:
    import anyio

    with anyio.CancelScope(shield=True), anyio.move_on_after(5):
        async with transaction(runtime.storage) as session:
            row = await lock(session, DesktopAssistanceRow, identity)
            if row is not None and row.operation_id == operation_id:
                row.state, row.failure = "blocked", "desktop_transition_unconfirmed"
                row.operation_expires_at = await now(session)


async def complete(runtime: Runtime, actor: Principal, workspace_id: str, identity: str, body: Completion) -> View:
    operation_id = new_object_id("dtop")
    async with transaction(runtime.storage) as session:
        row, control, selected = await authorized(session, runtime, actor, workspace_id, identity)
        current = await now(session)
        if row.state == "ready_to_resume":
            return View.model_validate(row)
        if body.epoch != row.epoch or (
            row.controller_id is not None and (row.controller_id, row.controller_session) != (actor.id, body.session_id)
        ):
            raise conflict("desktop_assistance", identity, "desktop_controller_required")
        if row.operation_expires_at is not None and row.operation_expires_at > current:
            raise conflict("desktop_assistance", identity, "desktop_transition_pending")
        execution = await session.get(DesktopExecutionRow, row.run_id)
        if (
            (row.cause == "user" and (execution is None or execution.inflight or execution.uncertain))
            or row.state in {"viewing", "preparing", "blocked"}
            or control.inflight
            or control.unmanaged_input
        ):
            raise conflict("desktop_assistance", identity, "desktop_execution_unconfirmed")
        assert row.generation is not None
        generation = row.generation
        grants = [(row.grant_id, row.epoch)] if row.grant_id else []
        viewers = (
            await session.scalars(
                select(DesktopViewerRow).where(
                    DesktopViewerRow.assistance_id == row.id, DesktopViewerRow.revoked.is_(False)
                )
            )
        ).all()
        grants.extend((viewer.id, viewer.epoch) for viewer in viewers)
        row.state = "revoking"
        row.operation_id, row.operation_expires_at = operation_id, current + timedelta(seconds=45)
    try:
        async with access_dispatch(runtime, selected, operation_id=operation_id) as send:
            for grant_id, epoch in grants:
                await send(wire.Inspect(operation="revoke", generation=generation, id=grant_id, epoch=epoch))
            await send(wire.Settle(generation=generation))
        async with transaction(runtime.storage) as session:
            row, _, _ = await authorized(session, runtime, actor, workspace_id, identity)
            if row.operation_id != operation_id:
                raise conflict("desktop_assistance", identity, "desktop_transition_superseded")
            run = await session.get(RunRow, row.run_id)
            if row.cause == "user" and run is not None and run.status == "completed":
                control = await lock(session, DesktopTargetRow, row.target_key)
                assert control is not None
                control.holder_id = None
                row.state = "viewing"
            else:
                row.state = "ready_to_resume"
            row.failure = None
            row.outcome = {
                "assistance_id": row.id,
                "outcome": body.outcome,
                "note": body.note,
                "instruction": "Observe the current desktop before further input; replan from the new screen.",
            }
            row.operation_id, row.operation_expires_at, row.lease_expires_at = None, None, None
            row.controller_id, row.controller_session, row.grant_id = None, None, None
            for viewer in (
                await session.scalars(select(DesktopViewerRow).where(DesktopViewerRow.assistance_id == row.id))
            ).all():
                viewer.revoked = True
            await audit_transition(session, actor, row, "complete")
            return View.model_validate(row)
    except BaseException as error:
        await blocked(runtime, identity, operation_id)
        if isinstance(error, Exception) and not isinstance(error, ServiceError):
            raise ServiceError(
                "unavailable", "Desktop transition could not be confirmed", {"dependency": "desktop_access"}
            ) from error
        raise


async def disconnect(runtime: Runtime, actor: Principal, workspace_id: str, identity: str, body: Control) -> View:
    """Closing one browser releases its access, without answering the deferred call."""
    operation_id = new_object_id("dtop")
    async with transaction(runtime.storage) as session:
        row, _, selected = await authorized(session, runtime, actor, workspace_id, identity, disconnecting=True)
        current = await now(session)
        if row.operation_expires_at is not None and row.operation_expires_at > current:
            raise conflict("desktop_assistance", identity, "desktop_transition_pending")
        owns_control = (row.controller_id, row.controller_session) == (actor.id, body.session_id)
        if owns_control and row.epoch != body.epoch:
            raise conflict("desktop_assistance", identity, "desktop_stale_epoch")
        viewers = (
            await session.scalars(
                select(DesktopViewerRow).where(
                    DesktopViewerRow.assistance_id == identity,
                    DesktopViewerRow.principal_id == actor.id,
                    DesktopViewerRow.session_id == body.session_id,
                    DesktopViewerRow.revoked.is_(False),
                )
            )
        ).all()
        grants = [(viewer.id, viewer.epoch) for viewer in viewers]
        if owns_control and row.grant_id:
            grants.append((row.grant_id, row.epoch))
        if not grants:
            return View.model_validate(row)
        assert row.generation is not None
        generation, prior_state = row.generation, row.state
        row.state, row.operation_id, row.operation_expires_at = (
            "revoking",
            operation_id,
            current + timedelta(seconds=45),
        )
    try:
        for grant_id, epoch in grants:
            await dispatch(
                runtime,
                selected,
                wire.Inspect(operation="revoke", generation=generation, id=grant_id, epoch=epoch),
                operation_id=operation_id,
            )
        async with transaction(runtime.storage) as session:
            row, _, _ = await authorized(session, runtime, actor, workspace_id, identity, disconnecting=True)
            if row.operation_id != operation_id:
                raise conflict("desktop_assistance", identity, "desktop_transition_superseded")
            for grant_id, _ in grants:
                viewer = await session.get(DesktopViewerRow, grant_id)
                if viewer:
                    viewer.revoked = True
            row.operation_id, row.operation_expires_at, row.failure = None, None, None
            row.state = "awaiting_user" if owns_control else prior_state
            if owns_control:
                row.controller_id, row.controller_session, row.grant_id, row.lease_expires_at = None, None, None, None
            await audit_transition(session, actor, row, "disconnect")
            return View.model_validate(row)
    except BaseException as error:
        await blocked(runtime, identity, operation_id)
        if isinstance(error, Exception) and not isinstance(error, ServiceError):
            raise ServiceError(
                "unavailable", "Desktop transition could not be confirmed", {"dependency": "desktop_access"}
            ) from error
        raise
