"""Discover usable instances and append authorized mounts to an executing Run."""

from typing import Literal

from a13n_harness.environment.models import EnvironmentPermissionSet
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import ObjectId
from a13n_service.resources.environment_instances.access import lock_environment_rows, require_usable
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments import mounts
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.environments.read_views import environment_view
from a13n_service.runs.environments.schemas import (
    DEFAULT_MOUNT_PERMISSIONS,
    MAX_MOUNTS,
    ClientConnectionObservation,
    Handle,
    MountCreate,
    MountName,
)
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.platform_tokens import Claims, lock_run
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.threads import refresh_version, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)


class Connect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: ObjectId
    alias: MountName
    persist_to_thread: bool = False
    expected_version: str | None = Field(
        default=None,
        min_length=1,
        description="The Thread version read from get_thread, required only when persist_to_thread is true.",
    )


class Connected(BaseModel):
    status: Literal["accepted"] = "accepted"
    run_id: str
    environment_id: str
    alias: str
    persisted_to_thread: bool
    thread_version: int | None
    application: Literal["next_model_request"] = "next_model_request"


class Connectable(BaseModel):
    environment_id: str
    name: str
    kind: str
    status: str
    client_connection: ClientConnectionObservation | None = None
    operations: EnvironmentPermissionSet
    operations_source: Literal["configured"] = "configured"


class ConnectablePage(BaseModel):
    items: list[Connectable]
    next_cursor: str | None


async def list_connectable(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    *,
    q: str | None,
    limit: int,
    cursor: str | None,
) -> ConnectablePage:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        query = select(EnvironmentRow).where(
            EnvironmentRow.workspace_id == scope.workspace_id,
            EnvironmentRow.status.not_in(("disabled", "deleting", "deleted")),
            or_(EnvironmentRow.failure.is_(None), EnvironmentRow.failure["permanent"].as_boolean().is_not(True)),
            or_(
                EnvironmentRow.kind == "external",
                EnvironmentRow.owner_principal_id.is_(None),
                EnvironmentRow.owner_principal_id == actor.id,
            ),
        )
        if q:
            query = query.where(EnvironmentRow.name.icontains(q, autoescape=True))
        rows, next_cursor = await cursors.id_page(
            session,
            query,
            EnvironmentRow.id,
            kind="connectable_environments",
            owner=cursors.query_owner(scope.workspace_id, actor.id, q),
            cursor=cursor,
            limit=limit,
        )
        # Batch metadata reads; descriptors are configuration evidence, never live probes.
        providers = (
            {
                row.id: row
                for row in await session.scalars(
                    select(EnvironmentProviderRow).where(
                        EnvironmentProviderRow.id.in_([row.provider_id for row in rows if row.provider_id])
                    )
                )
            }
            if any(row.provider_id for row in rows)
            else {}
        )
        templates = (
            {
                row.id: row
                for row in await session.scalars(
                    select(EnvironmentTemplateRow).where(
                        EnvironmentTemplateRow.id.in_(
                            [row.template_id for row in rows if row.template_id and row.handle is None]
                        )
                    )
                )
            }
            if any(row.template_id and row.handle is None for row in rows)
            else {}
        )
        settings = (
            {
                row.environment_id: row
                for row in await session.scalars(
                    select(ClientSettingsRow).where(
                        ClientSettingsRow.environment_id.in_([row.id for row in rows if row.kind == "external"])
                    )
                )
            }
            if any(row.kind == "external" for row in rows)
            else {}
        )
        items = []
        views = []
        for row in rows:
            if row.kind == "managed":
                assert row.provider_id is not None and row.template_id is not None
                provider = providers[row.provider_id]
                definition = runtime.registry.get("environment", provider.type)
                recipe = (
                    Handle.model_validate(row.handle).recipe
                    if row.handle
                    else (templates[row.template_id].config["recipe"] | (row.recipe_overrides or {}))
                )
            else:
                definition = WEBSOCKET_ENVD if row.kind == "external" else HTTP_ENVD
                recipe = {}
            descriptor = definition.describe_environment(definition.validate_environment(recipe))
            operations = DEFAULT_MOUNT_PERMISSIONS.operations & descriptor.permissions.operations
            if config := settings.get(row.id):
                operations &= EnvironmentPermissionSet.model_validate(config.permission_ceiling).operations
                if config.declared_capabilities is not None:
                    operations &= EnvironmentPermissionSet.model_validate(config.declared_capabilities).operations
            items.append(
                Connectable(
                    environment_id=row.id,
                    name=row.name,
                    kind=row.kind,
                    status=row.status,
                    operations=EnvironmentPermissionSet(operations=operations),
                )
            )
            views.append(environment_view(row))
    await runtime.client_connections.observe(views)
    for item, view in zip(items, views, strict=True):
        item.client_connection = view.client_connection
    return ConnectablePage(items=items, next_cursor=next_cursor)


async def connect(runtime: Runtime, claims: Claims, body: Connect) -> Connected:
    if body.alias in {mounts.PRIMARY, "builtin-skills", "builtin-instance"}:
        raise invalid("alias", "Choose a nonreserved mount name; connecting never changes the default mount")
    if (body.persist_to_thread and body.expected_version is None) or (
        not body.persist_to_thread and "expected_version" in body.model_fields_set
    ):
        raise invalid("expected_version", "Supply a Thread version exactly when persist_to_thread is true")
    async with transaction(runtime.storage) as session:
        thread, run, actor = await lock_run(runtime, session, claims)
        require_open(thread)
        if body.persist_to_thread:
            require_match(body.expected_version, thread.id, thread.version)
        desired = await session.get(ThreadEnvironmentRow, (thread.id, body.alias))
        accepted = tuple(EnvironmentMount.model_validate(m) for m in run.environment_mounts)
        current = next((m for m in accepted if m.name == body.alias), None)
        adding = current is None
        if current is not None and current.environment_id != body.environment_id:
            raise conflict("run", run.id, "mount_conflict", name=body.alias)
        if body.persist_to_thread and desired is not None and desired.environment_id != body.environment_id:
            raise conflict("thread", thread.id, "mount_conflict", name=body.alias)
        locked = await lock_environment_rows(session, run.workspace_id, [body.environment_id])
        require_usable(locked[body.environment_id], actor.id)
        selected = (
            desired
            if desired is not None and desired.environment_id == body.environment_id
            else MountCreate(
                name=body.alias,
                environment_id=body.environment_id,
            )
        )
        if current is None:
            if len(run.environment_mounts) >= MAX_MOUNTS:
                raise conflict("run", run.id, "mount_limit", limit=MAX_MOUNTS)
            [value] = await mounts.admit_mounts(
                session,
                thread,
                [selected],
                locked,
                principal=actor,
                existing=accepted,
                authority=ExecutionAuthority.model_validate(run.authority),
            )
            current = EnvironmentMount.model_validate(value)
        if not adding and body.persist_to_thread and desired is not None:
            [value] = await mounts.admit_mounts(
                session,
                thread,
                [desired],
                locked,
                principal=actor,
                authority=ExecutionAuthority.model_validate(run.authority),
            )
            if current != EnvironmentMount.model_validate(value):
                raise conflict("thread", thread.id, "mount_conflict", name=body.alias)
        persisted = desired is not None and desired.environment_id == body.environment_id
        if body.persist_to_thread and desired is None:
            # Reuse complete binding and mount admission, including limits and credential validation.
            await mounts.mount_environments(
                session,
                thread,
                [MountCreate.model_validate(current.model_dump(exclude={"session_version"}))],
                principal=actor,
            )
            persisted = True
            await refresh_version(session, thread)
        if adding:
            run.environment_mounts = [*run.environment_mounts, current.model_dump(mode="json")]
        record(
            session,
            WorkspaceScope(run.organization_id, run.workspace_id),
            actor_id=actor.id,
            action="run_environment.connect",
            target_kind="run",
            target_id=run.id,
            details={
                "environment_id": body.environment_id,
                "name": body.alias,
                "persist_to_thread": body.persist_to_thread,
            },
        )
        result = Connected(
            run_id=run.id,
            environment_id=body.environment_id,
            alias=body.alias,
            persisted_to_thread=persisted,
            thread_version=thread.version if body.persist_to_thread else None,
        )
    logger.info(
        "Run environment mount accepted",
        extra={
            "run_id": result.run_id,
            "environment_id": result.environment_id,
            "mount_name": result.alias,
            "persisted_to_thread": result.persisted_to_thread,
        },
    )
    return result
