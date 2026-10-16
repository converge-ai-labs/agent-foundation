"""Control-role reconciliation: lease loss never means successful input cleanup."""

from datetime import timedelta

from a13n_logging import get_logger
from sqlalchemy import select

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.sweeps import Sweep
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import execution_principals
from a13n_service.runs.desktop_assistance.admission import environment_key
from a13n_service.runs.desktop_assistance.service import blocked, dispatch
from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopTargetRow, DesktopViewerRow
from a13n_service.runs.environments.adapters import Target, provider_identity
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.access import principal_for, workspace_scope


async def allowed(session, runtime: Runtime, workspace_id: str, principal_id: str) -> bool:
    try:
        actor = await principal_for(session, runtime.access, principal_id)
        await workspace_scope(session, actor, workspace_id, "run")
        return True
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        return False


async def reconcile(runtime: Runtime, identity: str) -> None:
    operation_id = new_object_id("dtop")
    async with transaction(runtime.storage) as session:
        initial = await session.get(DesktopAssistanceRow, identity)
        if initial is None or initial.state == "abandoned":
            return
        if initial.state == "consumed":
            successor = await session.get(RunRow, initial.successor_id)
            if successor is not None and successor.status in {"accepted", "running"}:
                return
        run = await session.get(RunRow, initial.run_id)
        assert run is not None
        thread = await lock(session, ThreadRow, run.thread_id)
        assert thread is not None
        await session.refresh(run)
        environment = await lock(session, EnvironmentRow, initial.environment_id)
        control = await lock(session, DesktopTargetRow, initial.target_key)
        row = await lock(session, DesktopAssistanceRow, identity)
        assert control is not None and row is not None
        current = await now(session)
        if control.holder_id != row.id or (row.operation_expires_at is not None and row.operation_expires_at > current):
            return
        if row.state == "preparing" and run.status in {"accepted", "running"}:
            return
        principals = await execution_principals(session, runtime.access, [run])
        abandon = (
            row.deadline <= current
            or isinstance(principals.get(run.id), ServiceError)
            or thread.archived_at is not None
            or thread.last_run_id != run.id
            or run.status != "waiting"
        )
        viewers = (
            await session.scalars(
                select(DesktopViewerRow).where(
                    DesktopViewerRow.assistance_id == row.id, DesktopViewerRow.revoked.is_(False)
                )
            )
        ).all()
        revoke_viewers = [
            viewer
            for viewer in viewers
            if abandon
            or viewer.expires_at <= current
            or not await allowed(session, runtime, row.workspace_id, viewer.principal_id)
        ]
        revoke_control = row.grant_id is not None and (
            abandon
            or row.state in {"blocked", "revoking", "claiming"}
            or row.lease_expires_at is None
            or row.lease_expires_at <= current
            or (
                row.controller_id is not None
                and not await allowed(session, runtime, row.workspace_id, row.controller_id)
            )
        )
        if not abandon and not revoke_viewers and not revoke_control:
            return
        if (
            environment is None
            or environment_key(environment) != row.target_key
            or environment.status != "ready"
            or environment.provider_id is None
            or row.generation is None
            or control.inflight
        ):
            row.state, row.failure = "blocked", "desktop_cleanup_unconfirmed"
            return
        handle = Handle.model_validate(environment.handle)
        provider = await read_provider(session, EnvironmentProviderRow, environment.provider_id)
        if environment.provider_identity != provider_identity(runtime.registry, provider):
            row.state, row.failure = "blocked", "provider_identity_changed"
            return
        selected = Target(environment.id, provider, handle.recipe, handle.state)
        target_key = row.target_key
        generation = row.generation
        grants = [(viewer.id, viewer.epoch) for viewer in revoke_viewers]
        if revoke_control:
            assert row.grant_id is not None
            grants.append((row.grant_id, row.epoch))
        row.operation_id, row.operation_expires_at = operation_id, current + timedelta(seconds=45)
        prior_state = row.state
        row.state = "revoking"
    try:
        for grant_id, epoch in grants:
            await dispatch(
                runtime,
                selected,
                wire.Inspect(operation="revoke", generation=generation, id=grant_id, epoch=epoch),
                operation_id=operation_id,
            )
        if abandon or revoke_control:
            await dispatch(runtime, selected, wire.Settle(generation=generation), operation_id=operation_id)
        async with transaction(runtime.storage) as session:
            control = await lock(session, DesktopTargetRow, target_key)
            row = await lock(session, DesktopAssistanceRow, identity)
            if row is None or control is None or row.operation_id != operation_id:
                return
            for viewer_id, _ in grants:
                viewer = await session.get(DesktopViewerRow, viewer_id)
                if viewer is not None:
                    viewer.revoked = True
            row.operation_id, row.operation_expires_at, row.failure = None, None, None
            if abandon:
                row.state = "abandoned"
                row.outcome = {
                    "assistance_id": row.id,
                    "outcome": "abandoned",
                    "note": "Assistance expired or its wait/authority is no longer valid.",
                    "instruction": "Observe the current desktop before further input; replan from the new screen.",
                }
                control.holder_id = None
            elif revoke_control:
                row.state = "awaiting_user"
            else:
                row.state = prior_state
            if abandon or revoke_control:
                row.controller_id, row.controller_session, row.grant_id, row.lease_expires_at = None, None, None, None
    except BaseException:
        await blocked(runtime, identity, operation_id)
        raise


logger = get_logger(__name__)


class Reconciler:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.after: str | None = None

    async def __call__(self) -> None:
        async with transaction(self.runtime.storage) as session:
            query = (
                select(DesktopAssistanceRow.id)
                .join(DesktopTargetRow, DesktopTargetRow.holder_id == DesktopAssistanceRow.id)
                .where(DesktopAssistanceRow.state != "abandoned")
                .order_by(DesktopAssistanceRow.id)
                .limit(25)
            )
            if self.after:
                query = query.where(DesktopAssistanceRow.id > self.after)
            identities = (await session.scalars(query)).all()
        for identity in identities:
            self.after = identity
            try:
                await reconcile(self.runtime, identity)
            except Exception:
                logger.warning("Desktop assistance cleanup remains unconfirmed", extra={"assistance_id": identity})
                # Native uncertainty was persisted. Continue other independently expiring grants.
                continue
        if len(identities) < 25:
            self.after = None


def sweep(runtime: Runtime) -> Sweep:
    return Sweep(name="desktop_assistance", every=10, run=Reconciler(runtime), timeout=60)
