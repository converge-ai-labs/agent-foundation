"""Handback hints for actual blocked Computer callers, with frozen provenance."""

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbox import Claim, Handler, settle
from a13n_service.runs import inbox
from a13n_service.runs.environments.desktop import access, identity
from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow, DesktopControlRow
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.notices import StaleNotice
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope, authorize


async def suppress(session: AsyncSession, thread_id: str, *, mount_name: str | None = None) -> None:
    query = update(DesktopBlockedRow).where(DesktopBlockedRow.thread_id == thread_id)
    if mount_name is not None:
        query = query.where(DesktopBlockedRow.mount["name"].astext == mount_name)
    await session.execute(query.values(suppressed=True))


async def lock_targets(session: AsyncSession, registrations: Sequence[DesktopBlockedRow]) -> None:
    if not registrations:
        return
    # Acceptance subsequently freezes all desired mounts. Lock that complete set
    # before any target, including when several notices share one checkpoint cut.
    desired = await session.scalars(
        select(ThreadEnvironmentRow.environment_id).where(
            ThreadEnvironmentRow.thread_id.in_({item.thread_id for item in registrations})
        )
    )
    environments = set(desired) | {item.mount["environment_id"] for item in registrations}
    for environment_id in sorted(environments):
        await lock(session, EnvironmentRow, environment_id)
    for control_id in sorted({item.control_id for item in registrations}):
        await lock(session, DesktopControlRow, control_id)
    for registration in registrations:
        await session.refresh(registration)


async def eligible(session: AsyncSession, runtime: Runtime, registration: DesktopBlockedRow) -> RunRow | None:
    row = await session.get(DesktopControlRow, registration.control_id)
    # Checkpoint locks defer frozen inputs. Query explicitly so an identity-map
    # hit still loads the mounts needed to validate the registration's provenance.
    origin = await session.scalar(select(RunRow).where(RunRow.id == registration.run_id))
    thread = await session.get(ThreadRow, registration.thread_id)
    mount = EnvironmentMount.model_validate(registration.mount)
    environment = await session.get(EnvironmentRow, mount.environment_id)
    binding = await session.get(ThreadEnvironmentRow, (registration.thread_id, mount.name))
    current = await now(session)
    if (
        registration.suppressed
        or registration.expires_at is None
        or registration.expires_at <= current
        or row is None
        or row.control != "automation"
        or (row.generation, row.episode) != (registration.generation, registration.episode)
        or environment is None
        or identity.environment_key(environment) != row.target_key
        or origin is None
        or origin.cancel_requested_at is not None
        or origin.status == "cancelled"
        or registration.mount not in origin.environment_mounts
        or thread is None
        or thread.archived_at is not None
        or (thread.origin == "child" and thread.current_run_id is None)
        or binding is None
        or EnvironmentMount.model_validate(binding, from_attributes=True) != mount
    ):
        return None
    scope = WorkspaceScope(origin.organization_id, origin.workspace_id)
    authority = ExecutionAuthority.model_validate(origin.authority)
    try:
        principal = await principal_for(session, runtime.access, origin.principal_id, confinement=scope)
        authorize(principal, scope, "run", authority=authority)
        await access.target(session, runtime, environment, actor=principal, authority=authority)
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        return None
    return origin


async def require_notices(session: AsyncSession, runtime: Runtime, entries: Sequence[InboxEntryRow]) -> None:
    registrations = {
        entry.id: await session.get(DesktopBlockedRow, entry.payload["registration_id"]) for entry in entries
    }
    await lock_targets(session, [item for item in registrations.values() if item is not None])
    for entry in entries:
        registration = registrations[entry.id]
        if (
            registration is None
            or registration.entry_id != entry.id
            or await eligible(session, runtime, registration) is None
        ):
            raise StaleNotice(entry.id, "desktop")


def handbacks(runtime: Runtime) -> Handler:
    async def deliver(claimed: Claim) -> None:
        async with transaction(runtime.storage) as session:
            thread = await lock(session, ThreadRow, claimed.target["thread_id"])
            registration = await session.get(DesktopBlockedRow, claimed.payload["registration_id"])
            if thread is None or registration is None or registration.thread_id != thread.id:
                await settle(session, claimed, "dead", error="invalid_target")
                return
            await lock_targets(session, [registration])
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
                "control_id": registration.control_id,
                "mount": registration.mount,
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
            entry = await inbox.append_desktop_handback(session, thread, origin, payload)
            registration.entry_id = entry.id
            thread_id = thread.id
        from a13n_service.runs.accept import advance

        await advance(runtime, thread_id)

    return deliver
