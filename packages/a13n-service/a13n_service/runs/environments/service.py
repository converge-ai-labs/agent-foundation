"""Environment instances as workspace resources: reads, managed reservation, external target registration,
updates, stop and delete.

Stop and delete arbitrate with active use under the environment lock and only begin an operation; the provider
calls happen in the fenced lifecycle, never in the request. Registering an external target, or pointing it at a
new endpoint or token, verifies the target outside any transaction first.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.environments import external
from a13n_service.runs.environments.lifecycle import (
    PHASES,
    begin,
    in_use,
    mounted,
    require_usable_state,
    reserve,
    settled,
    supports,
    unusable,
)
from a13n_service.runs.environments.schemas import (
    EnvironmentPage,
    EnvironmentUpdate,
    EnvironmentView,
    ExternalTargetCreate,
    ManagedEnvironmentCreate,
)
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, allowed_verbs


def _audit(session: AsyncSession, actor: Principal, environment: EnvironmentRow, verb: str) -> None:
    record(
        session,
        WorkspaceScope(environment.organization_id, environment.workspace_id),
        actor_id=actor.id,
        action=f"environment.{verb}",
        target_kind="environment",
        target_id=environment.id,
    )


async def _find(
    session: AsyncSession, scope: WorkspaceScope, environment_id: str, *, lock: bool = False
) -> EnvironmentRow:
    query = select(EnvironmentRow).where(
        EnvironmentRow.workspace_id == scope.workspace_id, EnvironmentRow.id == environment_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found("environment", environment_id)
    return row


def _require_manager(actor: Principal, scope: WorkspaceScope, environment: EnvironmentRow) -> None:
    """A private external target is managed by its owner, or by a workspace administrator."""
    if environment.owner_principal_id not in {None, actor.id} and "admin" not in allowed_verbs(actor, scope):
        raise ServiceError("forbidden", "Private environments are managed by their owner", {"verb": "write"})


async def list_environments(
    storage: Storage, actor: Principal, workspace_id: str, *, status: str | None, limit: int, cursor: str | None
) -> EnvironmentPage:
    """Instances of the workspace; deleted tombstones only when asked for with `status=deleted`."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(EnvironmentRow).where(
                EnvironmentRow.workspace_id == scope.workspace_id,
                EnvironmentRow.status == status if status is not None else EnvironmentRow.status != "deleted",
            ),
            EnvironmentRow.id,
            kind="environments",
            owner=cursors.query_owner(scope.workspace_id, status),
            cursor=cursor,
            limit=limit,
        )
    return EnvironmentPage(items=[EnvironmentView.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_environment(
    storage: Storage, actor: Principal, workspace_id: str, environment_id: str
) -> EnvironmentView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return EnvironmentView.model_validate(await _find(session, scope, environment_id))


async def reserve_environment(
    runtime: Runtime, actor: Principal, workspace_id: str, body: ManagedEnvironmentCreate
) -> EnvironmentView:
    """A workspace-managed sandbox for threads to mount, reserved as acceptance reserves a primary sandbox.

    Maintenance dispatches its create operation. From then on the template's live idle policy applies as to any
    instance: `stop_after_seconds` without a run using it stops it and, while no thread mounts it either,
    `delete_after_seconds` deletes it; both count from its last use, becoming ready included.
    """
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        limit = runtime.settings.environments.managed_count
        environment = await reserve(session, actor, scope, body.template_id, limit=limit, name=body.name)
        await begin(session, environment, "creating")
        await session.flush()
        _audit(session, actor, environment, "create")
        return EnvironmentView.model_validate(environment)


async def register_external(
    runtime: Runtime, actor: Principal, workspace_id: str, body: ExternalTargetCreate
) -> EnvironmentView:
    """A private, connect-only external target: its device is verified outside any transaction, then recorded."""
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
    environment_id = new_object_id("env")
    token = body.token.get_secret_value()
    device_id = await external.verify(runtime, environment_id, body.endpoint, token, device_id=None)
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        environment = EnvironmentRow(
            id=environment_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            provider_id=None,
            template_id=None,
            device_id=device_id,
            endpoint=body.endpoint,
            owner_principal_id=actor.id,
            name=body.name or device_id,
            status="ready",
            generation=0,
            created_by_id=actor.id,
        )
        environment.token = external.seal(runtime.keys, environment, token)
        session.add(environment)
        await session.flush()
        _audit(session, actor, environment, "register")
        return EnvironmentView.model_validate(environment)


async def _writable(
    session: AsyncSession,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    if_match: str | None,
    *,
    lock: bool = True,
) -> EnvironmentRow:
    """The instance a write names, once its precondition holds and the actor may manage it."""
    scope = await workspace_scope(session, actor, workspace_id, "write")
    environment = await _find(session, scope, environment_id, lock=lock)
    require_match(if_match, environment.id, environment.version)
    _require_manager(actor, scope, environment)
    return environment


async def update_environment(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    body: EnvironmentUpdate,
    *,
    if_match: str | None,
) -> EnvironmentView:
    """Rename an instance, or point an external target at a new endpoint or token."""
    if body.token is not None:
        token = body.token.get_secret_value()
        return await _reconnect(runtime, actor, workspace_id, environment_id, body, token, if_match=if_match)
    if body.endpoint is not None:
        raise invalid("token", "a new endpoint needs its token")
    async with transaction(runtime.storage) as session:
        environment = await _writable(session, actor, workspace_id, environment_id, if_match)
        if body.name is not None and body.name != environment.name:
            environment.name = body.name
            await session.flush()
            _audit(session, actor, environment, "update")
        return EnvironmentView.model_validate(environment)


async def _reconnect(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    body: EnvironmentUpdate,
    token: str,
    *,
    if_match: str | None,
) -> EnvironmentView:
    """Store an external target's new endpoint or token, and its new name if any, once they reach its device.

    The device is verified outside any transaction; the precondition then holds for both the verification and the
    write.
    """
    field = "endpoint" if body.endpoint is not None else "token"
    async with short_session(runtime.storage) as session:
        environment = await _writable(session, actor, workspace_id, environment_id, if_match, lock=False)
        if environment.endpoint is None or environment.device_id is None:
            raise invalid(field, "only an external target has an endpoint and token")
        if environment.status == "deleted":
            raise conflict("environment", environment.id, "environment_deleted")
        endpoint, device_id = body.endpoint or environment.endpoint, environment.device_id
    await external.verify(runtime, environment_id, endpoint, token, device_id=device_id)
    async with transaction(runtime.storage) as session:
        environment = await _writable(session, actor, workspace_id, environment_id, if_match)
        environment.endpoint = endpoint
        environment.token = external.seal(runtime.keys, environment, token)
        if body.name is not None:
            environment.name = body.name
        await session.flush()
        _audit(session, actor, environment, "update")
        return EnvironmentView.model_validate(environment)


async def stop_environment(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, *, if_match: str | None
) -> EnvironmentView:
    """Begin stopping an idle ready instance; a run accepted afterwards waits for the stop, then starts it."""
    async with transaction(runtime.storage) as session:
        environment = await _writable(session, actor, workspace_id, environment_id, if_match)
        if environment.template_id is None or environment.provider_identity is None:
            raise conflict("environment", environment.id, "connect_only")
        if environment.status != "ready":
            raise conflict("environment", environment.id, f"environment_{environment.status}")
        require_usable_state(environment)
        if not supports(runtime.registry.get("environment", environment.provider_identity["type"]), "stopping"):
            raise conflict("environment", environment.id, "stop_unsupported")
        if await session.scalar(select(in_use(environment.id))):
            raise conflict("environment", environment.id, "in_use")
        await begin(session, environment, "stopping")
        await session.flush()
        _audit(session, actor, environment, "stop")
        return EnvironmentView.model_validate(environment)


async def delete_environment(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, *, if_match: str | None
) -> EnvironmentView:
    """Retire an instance no thread mounts and no active run uses; a managed one is destroyed by the lifecycle.

    An instance a permanent failure makes unusable, such as a lost one, also leaves the threads that mount it,
    so their next runs can use another. A different operation replaces an outstanding one only once it provably
    can no longer take effect.
    """
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        threads: list[str] = []
        if unusable(await _find(session, scope, environment_id)):
            # The threads whose mounts go with it precede the instance in the lock order.
            mounting = select(ThreadEnvironmentRow.thread_id).where(
                ThreadEnvironmentRow.environment_id == environment_id
            )
            threads = list(
                await session.scalars(
                    select(ThreadRow.id).where(ThreadRow.id.in_(mounting)).order_by(ThreadRow.id).with_for_update()
                )
            )
        environment = await _find(session, scope, environment_id, lock=True)
        require_match(if_match, environment.id, environment.version)
        _require_manager(actor, scope, environment)
        if environment.status in {"deleting", "deleted"}:
            return EnvironmentView.model_validate(environment)
        if threads and unusable(environment):
            await session.execute(
                delete(ThreadEnvironmentRow).where(
                    ThreadEnvironmentRow.environment_id == environment.id, ThreadEnvironmentRow.thread_id.in_(threads)
                )
            )
        if await session.scalar(select(mounted(environment.id))):
            raise conflict("environment", environment.id, "mounted")
        if await session.scalar(select(in_use(environment.id))):
            raise conflict("environment", environment.id, "in_use")
        if environment.status in PHASES and not settled(environment):
            raise conflict("environment", environment.id, "operation_unresolved")
        if environment.template_id is None or environment.provider_identity is None:
            # Nothing to destroy: an external target keeps running outside the Service, which drops its token,
            # and an unclaimed reservation never reached its provider.
            environment.status = "deleted"
            environment.operation_id = environment.operation_started_at = environment.operation_deadline = None
            environment.handle = environment.failure = environment.token = None
        elif not supports(runtime.registry.get("environment", environment.provider_identity["type"]), "deleting"):
            raise conflict("environment", environment.id, "destroy_unsupported")
        else:
            await begin(session, environment, "deleting")
        await session.flush()
        _audit(session, actor, environment, "delete")
        return EnvironmentView.model_validate(environment)
