"""Source administration, encrypted credentials and secret-free discovery."""

import hashlib
import json
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, assign, short_session, transaction, violated_constraint
from a13n_service.infra.errors import conflict, disabled, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.metrics import Destination, QueryRequest, QueryResult
from a13n_service.resources.metrics_data_sources.schemas import (
    Capabilities,
    Collector,
    CollectorCapability,
    Configuration,
    Credentials,
    DataSource,
    DataSourceCreate,
    DataSourcePage,
    DataSourceUpdate,
    DeploymentPreset,
    Query,
)
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.resources.runtime import Runtime
from a13n_service.tenancy.access import administering_workspace, workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

type Channel = Literal["export", "query"]
LOCAL_CACHE = Configuration(collection="proxy")
SOURCE_REFERENCE_CONSTRAINTS = {"fk_environments_metrics_data_source", "fk_monitoring_devices_metrics_data_source"}


def location(row: DataSourceRow, channel: Channel) -> SecretLocation:
    return SecretLocation(row.organization_id, row.__tablename__, f"{channel}_credentials", row.id)


def seal(keys: KeyRing, row: DataSourceRow, channel: Channel, value: Credentials | None) -> dict | None:
    if value is None or not value.headers:
        return None
    return keys.protect(json.dumps(value.plaintext()).encode(), location(row, channel)).model_dump()


def headers(keys: KeyRing, row: DataSourceRow, channel: Channel) -> dict[str, str]:
    envelope = row.export_credentials if channel == "export" else row.query_credentials
    if envelope is None:
        return {}
    return json.loads(keys.reveal(Envelope.model_validate(envelope), location(row, channel)))


async def ensure_local_cache(session: AsyncSession, scope: WorkspaceScope, actor_id: str) -> None:
    """Materialize the fixed preset once; concurrent discovery uses the unique workspace index."""
    await session.execute(
        insert(DataSourceRow)
        .values(
            id=new_object_id("mds"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            name="Local Cache",
            config=LOCAL_CACHE.model_dump(mode="json"),
            builtin=True,
            enabled=True,
            created_by_id=actor_id,
            updated_by_id=actor_id,
        )
        .on_conflict_do_nothing(index_elements=[DataSourceRow.workspace_id], index_where=text("builtin"))
    )


async def list_sources(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> DataSourcePage:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await ensure_local_cache(session, scope, actor.id)
        rows, next_cursor = await cursors.id_page(
            session,
            select(DataSourceRow).where(DataSourceRow.workspace_id == scope.workspace_id),
            DataSourceRow.id,
            kind="metrics_data_sources",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
        return DataSourcePage(items=[DataSource.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_source(storage: Storage, actor: Principal, workspace_id: str, source_id: str) -> DataSource:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return DataSource.model_validate(await find_row(session, actor, DataSourceRow, scope, source_id, "read"))


async def capabilities(runtime: Runtime, actor: Principal, workspace_id: str) -> Capabilities:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
    settings = runtime.settings.monitoring
    kinds: tuple[Collector, ...] = ("vmagent", "alloy")
    return Capabilities(
        proxy=settings.enabled,
        collectors=[
            CollectorCapability(
                kind=kind,
                available=settings.enabled and kind in settings.collectors,
                reason="monitoring_disabled"
                if not settings.enabled
                else ("not_configured" if kind not in settings.collectors else None),
            )
            for kind in kinds
        ],
    )


def _deployment_preset(runtime: Runtime, workspace_id: str) -> DeploymentPreset | None:
    settings = runtime.settings.monitoring
    preset = settings.deployment_preset
    if (
        not settings.enabled
        or preset is None
        or workspace_id not in preset.workspace_ids
        or preset.collector not in settings.collectors
    ):
        return None
    config = Configuration(collection="pull", collector=preset.collector, query=Query(endpoint=preset.query_endpoint))
    public = {
        "name": preset.name,
        "config": config.model_dump(mode="json"),
        "has_query_credentials": settings.deployment_query_bearer_token is not None,
    }
    revision = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    return DeploymentPreset.model_validate({**public, "revision": revision})


async def deployment_preset(runtime: Runtime, actor: Principal, workspace_id: str) -> DeploymentPreset | None:
    async with administering_workspace(
        runtime.storage, runtime.access, actor, workspace_id, action="metrics_data_source.preset", reading=True
    ):
        return _deployment_preset(runtime, workspace_id)


def _create_query_credentials(runtime: Runtime, workspace_id: str, body: DataSourceCreate) -> Credentials | None:
    if body.deployment_preset_revision is None:
        return body.query_credentials
    preset = _deployment_preset(runtime, workspace_id)
    if preset is None or preset.revision != body.deployment_preset_revision:
        raise invalid("deployment_preset_revision", "Deployment preset changed or is unavailable; reload the form")
    if not body.use_deployment_query_credentials:
        return body.query_credentials
    configured = runtime.settings.monitoring.deployment_preset
    token = runtime.settings.monitoring.deployment_query_bearer_token
    if configured is None or token is None:
        raise invalid("use_deployment_query_credentials", "Deployment query credentials are not configured")
    if body.config.query is None or body.config.query.endpoint != configured.query_endpoint:
        raise invalid(
            "use_deployment_query_credentials", "Deployment query credentials require their original endpoint"
        )
    return Credentials.model_validate({"headers": {"Authorization": "Bearer " + token.get_secret_value()}})


async def _validate_endpoints(runtime: Runtime, config: Configuration) -> None:
    for channel, destination in (("export", config.export), ("query", config.query)):
        if destination is not None:
            try:
                await runtime.endpoint_policy.validate(destination.endpoint)
            except ValueError:
                raise invalid(f"config.{channel}.endpoint", "the destination is not permitted") from None


async def create_source(runtime: Runtime, actor: Principal, workspace_id: str, body: DataSourceCreate) -> DataSource:
    action = "metrics_data_source.create"
    async with administering_workspace(
        runtime.storage, runtime.access, actor, workspace_id, action=action, reading=True
    ):
        pass
    query_credentials = _create_query_credentials(runtime, workspace_id, body)
    await _validate_endpoints(runtime, body.config)
    async with administering_workspace(runtime.storage, runtime.access, actor, workspace_id, action=action) as (
        session,
        scope,
    ):
        count = await session.scalar(
            select(func.count())
            .select_from(DataSourceRow)
            .where(
                DataSourceRow.workspace_id == scope.workspace_id,
                DataSourceRow.builtin.is_(False),
            )
        )
        if (count or 0) >= 128:
            raise conflict("workspace", scope.workspace_id, "metrics_data_source_limit", limit=128)
        row = DataSourceRow(
            id=new_object_id("mds"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            name=body.name,
            config=body.config.model_dump(mode="json"),
            enabled=body.enabled,
            builtin=False,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        row.export_credentials = seal(runtime.keys, row, "export", body.export_credentials)
        row.query_credentials = seal(runtime.keys, row, "query", query_credentials)
        session.add(row)
        audit_row(session, actor, row, "create")
        await session.flush()
        return DataSource.model_validate(row)


def _credentials_update(
    keys: KeyRing, row: DataSourceRow, body: DataSourceUpdate, config: Configuration
) -> dict[str, object]:
    previous = Configuration.model_validate(row.config)
    changes: dict[str, object] = {}
    channels: tuple[Channel, ...] = ("export", "query")
    for channel in channels:
        if channel == "export":
            old, new = previous.export, config.export
            stored, supplied = row.export_credentials, body.export_credentials
        else:
            old, new = previous.query, config.query
            stored, supplied = row.query_credentials, body.query_credentials
        field = f"{channel}_credentials"
        if new is None:
            if supplied is not None:
                raise invalid(field, "credentials require their destination")
            changes[field] = None
        elif field in body.model_fields_set:
            changes[field] = seal(keys, row, channel, supplied)
        elif stored is not None and old is not None and old.endpoint != new.endpoint:
            raise invalid(field, "replace or clear credentials when changing their destination")
    return changes


async def update_source(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    source_id: str,
    body: DataSourceUpdate,
    *,
    if_match: str | None,
) -> DataSource:
    action = "metrics_data_source.update"
    if body.config is not None:
        async with administering_workspace(
            runtime.storage, runtime.access, actor, workspace_id, action=action, reading=True
        ) as (session, scope):
            row = await find_row(session, actor, DataSourceRow, scope, source_id, "read")
            require_match(if_match, row.id, row.version)
        await _validate_endpoints(runtime, body.config)
    async with administering_workspace(runtime.storage, runtime.access, actor, workspace_id, action=action) as (
        session,
        scope,
    ):
        row = await find_row(session, actor, DataSourceRow, scope, source_id, "read", lock=True)
        require_match(if_match, row.id, row.version)
        if row.builtin:
            raise conflict(row.KIND, row.id, "builtin_data_source")
        config = body.config or Configuration.model_validate(row.config)
        values = given(body, "name", "enabled")
        values["config"] = config.model_dump(mode="json")
        values.update(_credentials_update(runtime.keys, row, body, config))
        changed = assign(row, values)
        if record_update(session, actor, row, changed):
            await session.flush()
        return DataSource.model_validate(row)


def query_destination(keys: KeyRing, row: DataSourceRow) -> Destination:
    if not row.enabled:
        raise disabled(row.KIND, row.id)
    config = Configuration.model_validate(row.config)
    if config.query is None:
        raise conflict(row.KIND, row.id, "query_not_configured")
    return Destination(config.query.endpoint, headers(keys, row, "query"))


async def query_source(
    runtime: Runtime, actor: Principal, workspace_id: str, source_id: str, body: QueryRequest
) -> QueryResult:
    async with administering_workspace(
        runtime.storage, runtime.access, actor, workspace_id, action="metrics_data_source.query", reading=True
    ) as (session, scope):
        row = await find_row(session, actor, DataSourceRow, scope, source_id, "read")
        destination = query_destination(runtime.keys, row)
    return await runtime.metrics_queries.execute(runtime.endpoint_policy, destination, body)


async def delete_source(
    runtime: Runtime, actor: Principal, workspace_id: str, source_id: str, *, if_match: str | None
) -> None:
    try:
        async with administering_workspace(
            runtime.storage, runtime.access, actor, workspace_id, action="metrics_data_source.delete"
        ) as (session, scope):
            row = await find_row(session, actor, DataSourceRow, scope, source_id, "read", lock=True)
            require_match(if_match, row.id, row.version)
            if row.builtin:
                raise conflict(row.KIND, row.id, "builtin_data_source")
            audit_row(session, actor, row, "delete")
            await session.delete(row)
            await session.flush()
    except IntegrityError as error:
        if violated_constraint(error) not in SOURCE_REFERENCE_CONSTRAINTS:
            raise
        raise conflict("metrics_data_source", source_id, "data_source_in_use") from None
