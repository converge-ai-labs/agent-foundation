"""Administrative registrations, scoped reads, and dedicated carrier authentication."""

import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import assign, short_session, violated_constraint
from a13n_service.infra.errors import ServiceError, conflict, disabled, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.metrics_data_sources.schemas import Configuration
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.runs.devices.schemas import Device, DeviceCreate, DeviceCredential, DevicePage, DeviceUpdate
from a13n_service.runs.devices.tables import DeviceRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import administering_workspace, workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.tables import WorkspaceRow


async def validate_source(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, source_id: str | None
) -> None:
    if source_id is None:
        return
    source = await find_row(session, actor, DataSourceRow, scope, source_id, "read")
    if not source.enabled:
        raise disabled(source.KIND, source.id)
    if Configuration.model_validate(source.config).collection == "pull":
        raise invalid(
            "metrics_data_source_id",
            "Reverse Devices cannot be scraped; choose proxy or operator-managed native/external ingestion",
        )


async def list_devices(
    runtime: Runtime, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> DevicePage:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(DeviceRow).where(DeviceRow.workspace_id == scope.workspace_id),
            DeviceRow.id,
            kind="monitoring_devices",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
        return DevicePage(items=[Device.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_device(runtime: Runtime, actor: Principal, workspace_id: str, device_id: str) -> Device:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return Device.model_validate(await find_row(session, actor, DeviceRow, scope, device_id, "read"))


async def create_device(runtime: Runtime, actor: Principal, workspace_id: str, body: DeviceCreate) -> DeviceCredential:
    try:
        async with administering_workspace(
            runtime.storage, runtime.access, actor, workspace_id, action="monitoring_device.create"
        ) as (session, scope):
            await validate_source(session, actor, scope, body.metrics_data_source_id)
            count = await session.scalar(
                select(func.count()).select_from(DeviceRow).where(DeviceRow.workspace_id == scope.workspace_id)
            )
            if (count or 0) >= 512:
                raise conflict("workspace", scope.workspace_id, "monitoring_device_limit", limit=512)
            token = new_secret()
            row = DeviceRow(
                id=new_object_id("dev"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                name=body.name,
                device_id=body.device_id,
                metrics_data_source_id=body.metrics_data_source_id,
                token_hash=secret_hash(token),
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            session.add(row)
            audit_row(session, actor, row, "create")
            await session.flush()
            return DeviceCredential(device=Device.model_validate(row), token=token)
    except IntegrityError as error:
        if violated_constraint(error) != "uq_monitoring_devices_identity":
            raise
        raise conflict("workspace", workspace_id, "device_identity_registered") from None


async def update_device(
    runtime: Runtime, actor: Principal, workspace_id: str, device_id: str, body: DeviceUpdate, *, if_match: str | None
) -> Device:
    async with administering_workspace(
        runtime.storage, runtime.access, actor, workspace_id, action="monitoring_device.update"
    ) as (session, scope):
        row = await find_row(session, actor, DeviceRow, scope, device_id, "read", lock=True)
        require_match(if_match, row.id, row.version)
        values = given(body, "name", "enabled")
        if "metrics_data_source_id" in body.model_fields_set:
            await validate_source(session, actor, scope, body.metrics_data_source_id)
            values["metrics_data_source_id"] = body.metrics_data_source_id
        changed = assign(row, values)
        if record_update(session, actor, row, changed):
            await session.flush()
        return Device.model_validate(row)


async def rotate(
    runtime: Runtime, actor: Principal, workspace_id: str, device_id: str, *, if_match: str | None
) -> DeviceCredential:
    async with administering_workspace(
        runtime.storage, runtime.access, actor, workspace_id, action="monitoring_device.rotate"
    ) as (session, scope):
        row = await find_row(session, actor, DeviceRow, scope, device_id, "read", lock=True)
        require_match(if_match, row.id, row.version)
        token = new_secret()
        row.token_hash = secret_hash(token)
        row.updated_by_id = actor.id
        audit_row(session, actor, row, "rotate")
        await session.flush()
        return DeviceCredential(device=Device.model_validate(row), token=token)


async def delete_device(
    runtime: Runtime, actor: Principal, workspace_id: str, device_id: str, *, if_match: str | None
) -> None:
    async with administering_workspace(
        runtime.storage, runtime.access, actor, workspace_id, action="monitoring_device.delete"
    ) as (session, scope):
        row = await find_row(session, actor, DeviceRow, scope, device_id, "read", lock=True)
        require_match(if_match, row.id, row.version)
        audit_row(session, actor, row, "delete")
        await session.delete(row)


async def authenticate(runtime: Runtime, device_id: str, token: str) -> str:
    """Return a detached credential fence, with no database scope across the carrier."""
    async with short_session(runtime.storage) as session:
        row = await session.scalar(
            select(DeviceRow)
            .join(WorkspaceRow, WorkspaceRow.id == DeviceRow.workspace_id)
            .where(DeviceRow.id == device_id, DeviceRow.enabled.is_(True), WorkspaceRow.archived_at.is_(None))
        )
        if row is None or not secrets.compare_digest(row.token_hash, secret_hash(token)):
            raise ServiceError("unauthenticated", "Invalid Device attachment credential")
        return row.token_hash
