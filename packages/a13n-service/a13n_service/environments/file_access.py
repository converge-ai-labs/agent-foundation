"""Bounded access to an existing target without creating or replacing a sandbox."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

from a13n_harness.providers.environment.files import FileOperator
from a13n_harness.providers.environment.models import EnvironmentState
from anyio import fail_after
from sqlalchemy import delete

from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .access import authorize_environment_resource
from .configuration import load_configuration
from .lifecycle import EnvironmentLifecycle, LifecycleOperation
from .models import EnvironmentFileUseRecord, EnvironmentProviderRecord, EnvironmentRecord
from .retention import refresh_retention


class ExistingEnvironmentFiles:
    def __init__(self, lifecycle: EnvironmentLifecycle) -> None:
        self.lifecycle = lifecycle

    @asynccontextmanager
    async def open(
        self,
        *,
        actor: AuthenticatedActor,
        environment_id: str,
        backing_identity: str,
        authorize: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[FileOperator]:
        lease_id = new_object_id("envuse")
        deadline = utc_now() + timedelta(seconds=40)
        await authorize()
        async with transaction(self.lifecycle.sessions) as session:
            row = await session.get(EnvironmentRecord, environment_id, with_for_update=True)
            if row is None:
                raise ValueError("Memory Environment is unavailable")
            await authorize_environment_resource(
                session,
                actor=actor,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                action=WorkspaceAction.environment_use,
            )
            provider = await session.get(EnvironmentProviderRecord, row.provider_id)
            if (
                row.status != "running"
                or row.operation_id is not None
                or f"{row.id}:{row.generation}" != backing_identity
                or provider is None
                or not provider.enabled
            ):
                raise ValueError("Memory Environment is unavailable")
            configuration = await load_configuration(session, row)
            operation = LifecycleOperation(
                environment_id=row.id,
                provider_type=provider.type,
                provider_configuration=provider.configuration,
                credential=provider.credential_snapshot(),
                configuration=configuration,
                # Stateless Providers reconstruct the retained target from its configuration.
                state=EnvironmentState.model_validate(row.state) if row.state is not None else None,
                operation_id=lease_id,
                fence=row.operation_generation,
                owner=lease_id,
                action="reconcile",
                previous_status=row.status,
            )
            await session.execute(
                delete(EnvironmentFileUseRecord).where(
                    EnvironmentFileUseRecord.environment_id == row.id, EnvironmentFileUseRecord.expires_at <= utc_now()
                )
            )
            session.add(EnvironmentFileUseRecord(id=lease_id, environment_id=row.id, expires_at=deadline))
            await refresh_retention(session, row, utc_now())
        environment = None
        try:
            with fail_after(30):
                environment = await self.lifecycle.construct(operation, allow_create=False)
                await environment.enter(mount_id=lease_id)
                await environment.prepare()
                await environment.check_ready(frozenset({"files"}))
                if environment.dump_state() != operation.state or environment.operations.files is None:
                    raise ValueError("Memory Environment identity changed")
                await authorize()
                async with short_session(self.lifecycle.sessions) as session:
                    current = await session.get(EnvironmentRecord, environment_id)
                    lease = await session.get(EnvironmentFileUseRecord, lease_id)
                    if (
                        current is None
                        or lease is None
                        or assume_utc(lease.expires_at) <= utc_now()
                        or f"{current.id}:{current.generation}" != backing_identity
                        or current.status != "running"
                    ):
                        raise ValueError("Memory Environment lease changed")
                yield environment.operations.files
                await authorize()
        finally:
            with fail_after(5, shield=True):
                if environment is not None:
                    await environment.close()
                async with transaction(self.lifecycle.sessions) as session:
                    row = await session.get(EnvironmentRecord, environment_id, with_for_update=True)
                    await session.execute(
                        delete(EnvironmentFileUseRecord).where(EnvironmentFileUseRecord.id == lease_id)
                    )
                    if row is not None:
                        await refresh_retention(session, row, utc_now())
