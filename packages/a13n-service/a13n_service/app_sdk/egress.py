"""Create and maintain workspace credentials owned by an installed App."""

from collections.abc import Sequence

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import invalid, not_found
from a13n_service.resources.egress_credentials import service
from a13n_service.resources.egress_credentials.schemas import Credential as Credential
from a13n_service.resources.egress_credentials.schemas import CredentialCreate, CredentialRotation
from a13n_service.resources.egress_credentials.schemas import SecretReference as SecretReference
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.runs.runtime import Runtime

from ._access import app_actor, app_workspace


async def create_app_credential(
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    name: str,
    description: str = "",
    allowed_hosts: Sequence[str],
    value: str | SecretStr,
) -> Credential:
    """Create an encrypted credential. Its App ownership and allowed hosts are immutable."""
    async with transaction(runtime.storage) as session:
        return await create_app_credential_in_transaction(
            session,
            runtime,
            workspace_id,
            app_id=app_id,
            actor_id=actor_id,
            name=name,
            description=description,
            allowed_hosts=allowed_hosts,
            value=value,
        )


async def create_app_credential_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    name: str,
    description: str = "",
    allowed_hosts: Sequence[str],
    value: str | SecretStr,
) -> Credential:
    """Create an owned credential and its audit event in the caller transaction."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    body = CredentialCreate(
        name=name,
        description=description,
        allowed_hosts=tuple(allowed_hosts),
        value=SecretStr(value) if isinstance(value, str) else value,
    )
    if len(app_id) > 128:
        raise invalid("app_id", "Credential owner ID must be at most 128 characters")
    actor = await app_actor(session, runtime, scope, actor_id)
    return await service.insert_credential(session, runtime.keys, actor, scope, body, app_id=app_id)


async def _owned(
    session: AsyncSession,
    workspace_id: str,
    app_id: str,
    credential_id: str,
    *,
    lock: bool = False,
) -> CredentialRow | None:
    query = select(CredentialRow).where(
        CredentialRow.id == credential_id,
        CredentialRow.workspace_id == workspace_id,
        CredentialRow.app_id == app_id,
    )
    return await session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


async def rotate_app_credential(
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    credential_id: str,
    value: str | SecretStr,
) -> Credential:
    """Replace the secret and notify active Sessions after commit, without changing its identity."""
    async with transaction(runtime.storage) as session:
        return await rotate_app_credential_in_transaction(
            session, runtime, workspace_id, app_id=app_id, actor_id=actor_id, credential_id=credential_id, value=value
        )


async def rotate_app_credential_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    credential_id: str,
    value: str | SecretStr,
) -> Credential:
    """Rotate an owned credential; notify active Sessions only after the caller commits."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    body = CredentialRotation(value=SecretStr(value) if isinstance(value, str) else value)
    actor = await app_actor(session, runtime, scope, actor_id)
    row = await _owned(session, workspace_id, app_id, credential_id, lock=True)
    if row is None:
        raise not_found(CredentialRow.KIND, credential_id)
    return await service.rotate_locked(session, runtime.keys, actor, row, body, redis=runtime.redis)


async def revoke_app_credential(
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    credential_id: str,
) -> Credential:
    """Permanently revoke an owned credential and notify active Sessions after commit."""
    async with transaction(runtime.storage) as session:
        return await revoke_app_credential_in_transaction(
            session, runtime, workspace_id, app_id=app_id, actor_id=actor_id, credential_id=credential_id
        )


async def revoke_app_credential_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    actor_id: str,
    credential_id: str,
) -> Credential:
    """Revoke an owned credential; notify active Sessions only after the caller commits."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await app_actor(session, runtime, scope, actor_id)
    row = await _owned(session, workspace_id, app_id, credential_id, lock=True)
    if row is None:
        raise not_found(CredentialRow.KIND, credential_id)
    return await service.revoke_locked(session, actor, row, redis=runtime.redis)


async def get_app_credential(
    runtime: Runtime, workspace_id: str, *, app_id: str, credential_id: str
) -> Credential | None:
    """Return metadata only; missing and differently owned credentials are indistinguishable."""
    async with short_session(runtime.storage) as session:
        await app_workspace(session, runtime, workspace_id, app_id, write=False)
        row = await _owned(session, workspace_id, app_id, credential_id)
        return None if row is None else Credential.model_validate(row)
