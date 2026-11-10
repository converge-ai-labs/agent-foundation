"""Credential management in short transactions; secret values never enter resource views."""

from collections.abc import Sequence
from datetime import UTC, datetime
from functools import partial

from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, after_commit, assign, short_session, transaction
from a13n_service.infra.errors import at_field, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.egress_credentials.notifications import notify
from a13n_service.resources.egress_credentials.schemas import (
    Credential,
    CredentialCreate,
    CredentialPage,
    CredentialRotation,
    CredentialUpdate,
)
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.resources.environment_instances.session_configuration import SecretReference
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize


def location(row: CredentialRow) -> SecretLocation:
    return SecretLocation(row.organization_id, "egress_credentials", "value", row.id)


def reveal(keys: KeyRing, row: CredentialRow) -> SecretStr:
    require_active(row)
    assert row.value is not None
    return SecretStr(keys.reveal(Envelope.model_validate(row.value), location(row)).decode())


def require_active(row: CredentialRow) -> None:
    if row.state != "active":
        raise conflict(row.KIND, row.id, "credential_revoked")


async def validate_references(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    references: Sequence[SecretReference],
    *,
    verb: Verb = "run",
    authority: ExecutionAuthority | None = None,
) -> None:
    """Check one selection in a bounded read without decrypting or freezing token values."""
    if not references:
        return
    authorize(actor, scope, verb, authority=authority)
    rows = {
        row.id: row
        for row in await session.scalars(
            select(CredentialRow).where(
                CredentialRow.workspace_id == scope.workspace_id,
                CredentialRow.id.in_({binding.credential_id for binding in references}),
            )
        )
    }
    for index, binding in enumerate(references):
        with at_field(f"egress.secrets.{index}"):
            row = rows.get(binding.credential_id)
            if row is None:
                raise not_found(CredentialRow.KIND, binding.credential_id)
            require_active(row)
            if not set(binding.inject_hosts) <= set(row.allowed_hosts):
                raise invalid("inject_hosts", "Injection hosts exceed the credential's allowed hosts")


async def create_credential(
    storage: Storage, keys: KeyRing, actor: Principal, workspace_id: str, body: CredentialCreate
) -> Credential:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = CredentialRow(
            id=new_object_id("ecred"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            name=body.name,
            description=body.description,
            allowed_hosts=list(body.allowed_hosts),
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        row.value = keys.protect(body.value.get_secret_value().encode(), location(row)).model_dump(mode="json")
        session.add(row)
        audit_row(session, actor, row, "create", {"credential_version": 1})
        await session.flush()
        return Credential.model_validate(row)


async def list_credentials(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> CredentialPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.keyset_page(
            session,
            select(CredentialRow).where(CredentialRow.workspace_id == scope.workspace_id),
            (CredentialRow.created_at, CredentialRow.id),
            kind="egress_credentials",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return CredentialPage(items=[Credential.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_credential(storage: Storage, actor: Principal, workspace_id: str, credential_id: str) -> Credential:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, CredentialRow, scope, credential_id, "read")
        return Credential.model_validate(row)


async def update_credential(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    credential_id: str,
    body: CredentialUpdate,
    *,
    if_match: str | None,
) -> Credential:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, CredentialRow, scope, credential_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if record_update(session, actor, row, assign(row, given(body, "name", "description"))):
            await session.flush()
        return Credential.model_validate(row)


async def rotate_credential(
    storage: Storage,
    keys: KeyRing,
    actor: Principal,
    workspace_id: str,
    credential_id: str,
    body: CredentialRotation,
    *,
    if_match: str | None,
    redis: Redis,
) -> Credential:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, CredentialRow, scope, credential_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        require_active(row)
        row.value = keys.protect(body.value.get_secret_value().encode(), location(row)).model_dump(mode="json")
        row.credential_version += 1
        row.updated_by_id = actor.id
        audit_row(session, actor, row, "rotate", {"credential_version": row.credential_version})
        after_commit(session, partial(notify, redis, row.id, row.credential_version))
        await session.flush()
        return Credential.model_validate(row)


async def revoke_credential(
    storage: Storage, actor: Principal, workspace_id: str, credential_id: str, *, if_match: str | None, redis: Redis
) -> Credential:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, CredentialRow, scope, credential_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        require_active(row)
        row.value = None
        row.state = "revoked"
        row.revoked_at = datetime.now(UTC)
        row.credential_version += 1
        row.updated_by_id = actor.id
        audit_row(session, actor, row, "revoke", {"credential_version": row.credential_version})
        after_commit(session, partial(notify, redis, row.id, row.credential_version))
        await session.flush()
        return Credential.model_validate(row)
