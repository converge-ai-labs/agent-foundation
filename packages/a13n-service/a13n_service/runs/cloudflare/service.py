"""Workspace authorization and durable state transitions for managed gateways."""

from datetime import timedelta
from secrets import token_hex, token_urlsafe
from typing import Literal, cast
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.resources.rows import audit_row, record_update
from a13n_service.runs.environments.provider_connections import _references
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import refuse_archived, workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from a13n_service.tenancy.cloudflare_deployments import DeploymentRow
from a13n_service.tenancy.tables import WorkspaceRow

from .schemas import Deployment, DeploymentCreate, DeploymentList, DeploymentRetry

CLEANUP = ("smoke", "application", "namespace", "worker", "image")
ACTIVE = ("deploying", "retiring")
LEASE = timedelta(minutes=8)


def location(row: DeploymentRow, column: str) -> SecretLocation:
    return SecretLocation(row.organization_id, row.__tablename__, column, row.id)


def view(row: DeploymentRow) -> Deployment:
    return Deployment(
        id=row.id,
        version=row.version,
        provider_id=row.provider_id,
        name=row.name,
        account_id=row.account_id,
        worker_name=row.worker_name,
        source_image=row.source_image,
        status=cast(Literal["deploying", "ready", "retiring", "needs_attention", "retired"], row.status),
        operation=cast(Literal["deploy", "retire"], row.operation),
        stage=row.stage,
        endpoint=row.resources.get("endpoint"),
        error=row.error,
        cleanup={
            key: "not_created"
            if row.resources.get(f"removed_{key}") == "not_created"
            else "removed"
            if row.resources.get(f"removed_{key}")
            else "pending"
            for key in CLEANUP
        },
    )


def audit(session: AsyncSession, row: DeploymentRow, action: str, actor: Principal | None = None) -> None:
    record(
        session,
        WorkspaceScope(row.organization_id, row.workspace_id),
        actor_id=actor.id if actor else None,
        action=action,
        target_kind=row.KIND,
        target_id=row.id,
    )


def require_enabled(runtime: Runtime) -> None:
    if not runtime.settings.provisioning.cloudflare.enabled:
        raise ServiceError("unavailable", "Managed Cloudflare deployment is disabled on this Service")


async def list_deployments(runtime: Runtime, actor: Principal, workspace_id: str) -> DeploymentList:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "admin")
        rows = await session.scalars(
            select(DeploymentRow)
            .where(DeploymentRow.workspace_id == workspace_id, DeploymentRow.status != "retired")
            .order_by(DeploymentRow.created_at.desc(), DeploymentRow.id)
        )
        return DeploymentList(
            available=runtime.settings.provisioning.cloudflare.enabled, items=[view(row) for row in rows]
        )


async def find(session: AsyncSession, workspace_id: str, deployment_id: str, *, lock: bool = False) -> DeploymentRow:
    query = select(DeploymentRow).where(DeploymentRow.workspace_id == workspace_id, DeploymentRow.id == deployment_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise not_found(DeploymentRow.KIND, deployment_id)
    return row


async def get_deployment(runtime: Runtime, actor: Principal, workspace_id: str, deployment_id: str) -> Deployment:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "admin")
        return view(await find(session, workspace_id, deployment_id))


async def create(runtime: Runtime, actor: Principal, workspace_id: str, body: DeploymentCreate) -> Deployment:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        query = select(DeploymentRow).where(
            DeploymentRow.workspace_id == workspace_id, DeploymentRow.request_id == str(body.request_id)
        )
        existing = await session.scalar(query)
        if existing is None:
            require_enabled(runtime)
            workspace = await session.get_one(WorkspaceRow, workspace_id, with_for_update=True, populate_existing=True)
            refuse_archived(workspace)
            public_url = urlsplit(runtime.settings.server.public_url)
            row = DeploymentRow(
                id=new_object_id("cfd"),
                workspace_id=workspace_id,
                organization_id=scope.organization_id,
                request_id=str(body.request_id),
                account_id=body.account_id,
                name=body.name,
                worker_name=f"a13n-env-{token_hex(16)}",
                source_image=runtime.settings.provisioning.cloudflare.image,
                service_origin=f"https://{public_url.netloc}" if public_url.scheme == "https" else None,
                status="deploying",
                operation="deploy",
                stage="preflight",
                resources={},
                due_at=await now(session),
                attempts=0,
            )
            row.deployment_token = runtime.keys.protect(
                body.api_token.get_secret_value().encode(), location(row, "deployment_token")
            ).model_dump()
            row.gateway_token = runtime.keys.protect(
                token_urlsafe(32).encode(), location(row, "gateway_token")
            ).model_dump()
            columns = type(row).__table__.columns.keys()
            values = {key: row.__dict__[key] for key in columns if key in row.__dict__}
            created = await session.scalar(
                insert(DeploymentRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[DeploymentRow.workspace_id, DeploymentRow.request_id])
                .returning(DeploymentRow)
            )
            if created is not None:
                audit(session, created, "create", actor)
                return view(created)
            existing = (await session.scalars(query)).one()
        if (existing.name, existing.account_id) != (body.name, body.account_id):
            raise conflict(existing.KIND, existing.id, "deployment_request_changed")
        return view(existing)


async def retry(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    deployment_id: str,
    body: DeploymentRetry,
    if_match: str | None,
) -> Deployment:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "admin")
        require_enabled(runtime)
        row = await find(session, workspace_id, deployment_id, lock=True)
        require_match(if_match, row.id, row.version)
        if row.status != "needs_attention":
            raise conflict(row.KIND, row.id, "deployment_not_waiting_for_retry")
        if body.api_token is not None:
            row.deployment_token = runtime.keys.protect(
                body.api_token.get_secret_value().encode(), location(row, "deployment_token")
            ).model_dump()
        row.status = "deploying" if row.operation == "deploy" else "retiring"
        row.attempts, row.error, row.due_at = 0, None, await now(session)
        audit(session, row, "retry", actor)
        await session.flush()
        return view(row)


async def retire(
    runtime: Runtime, actor: Principal, workspace_id: str, deployment_id: str, if_match: str | None
) -> Deployment:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "admin")
        row = await find(session, workspace_id, deployment_id, lock=True)
        require_match(if_match, row.id, row.version)
        if row.operation == "retire":
            return view(row)
        require_enabled(runtime)
        if row.status not in {"ready", "needs_attention"}:
            raise conflict(row.KIND, row.id, "deployment_in_progress")
        if row.provider_id:
            # Serialize with ordinary Provider edits and new references before disabling it.
            provider = await session.get(EnvironmentProviderRow, row.provider_id, with_for_update=True)
            assert provider is not None
            refs = await _references(session, workspace_id, provider.id)
            if refs.template_count or refs.environment_count:
                raise conflict(provider.KIND, provider.id, "provider_in_use", references=refs.model_dump(mode="json"))
            if provider.enabled:
                provider.enabled = False
                record_update(session, actor, provider, ["enabled"])
        row.operation, row.status, row.stage = "retire", "retiring", "smoke"
        row.attempts, row.error, row.due_at = 0, None, await now(session)
        audit(session, row, "retire", actor)
        await session.flush()
        return view(row)


async def remove_provider(session: AsyncSession, row: DeploymentRow) -> None:
    if row.provider_id is None:
        return
    provider = await session.get(EnvironmentProviderRow, row.provider_id, with_for_update=True)
    assert provider is not None
    refs = await _references(session, row.workspace_id, provider.id)
    if refs.template_count or refs.environment_count or provider.enabled:
        raise conflict(provider.KIND, provider.id, "provider_in_use")
    row.provider_id = None
    await session.flush()
    audit_row(session, None, provider, "delete")
    await session.delete(provider)
