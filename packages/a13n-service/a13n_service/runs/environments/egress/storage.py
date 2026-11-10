"""Session credential references and confirmed application; callers own all write fences."""

from datetime import UTC, datetime
from decimal import Decimal

from a13n_envd_client.eip.v1 import SessionDescriptor
from a13n_harness.environment.models import EnvironmentError
from pydantic import SecretStr
from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.resources.environment_instances.session_configuration import SessionConfiguration
from a13n_service.runs.environments.egress.state import Credential, Snapshot
from a13n_service.runs.environments.egress.tables import SessionCredentialRow
from a13n_service.tenancy.authorize import WorkspaceScope


async def load(storage: Storage, keys: KeyRing, scope: WorkspaceScope, configuration: SessionConfiguration) -> Snapshot:
    references = configuration.credential_references
    if not references:
        return Snapshot(configuration, {})
    async with short_session(storage) as session:
        rows = list(
            await session.execute(
                select(
                    CredentialRow.id,
                    CredentialRow.credential_version,
                    CredentialRow.state,
                    CredentialRow.allowed_hosts,
                    CredentialRow.value,
                ).where(
                    CredentialRow.organization_id == scope.organization_id,
                    CredentialRow.workspace_id == scope.workspace_id,
                    CredentialRow.id.in_({binding.credential_id for binding in references}),
                )
            )
        )
    found = {row.id: row for row in rows}
    for binding in references:
        row = found.get(binding.credential_id)
        if row is None or not set(binding.inject_hosts) <= set(row.allowed_hosts):
            raise EnvironmentError("The Session credential selection is no longer valid", code="credential_unavailable")
    credentials = {}
    for row in rows:
        value = None
        if row.state == "active":
            value = SecretStr(
                keys.reveal(
                    Envelope.model_validate(row.value),
                    SecretLocation(scope.organization_id, "egress_credentials", "value", row.id),
                ).decode()
            )
        credentials[row.id] = Credential(row.id, row.credential_version, value)
    return Snapshot(configuration, credentials)


async def register(
    session: AsyncSession,
    scope: WorkspaceScope,
    thread_id: str,
    environment_id: str,
    binding_name: str,
    version: int,
    configuration: SessionConfiguration,
) -> None:
    if not configuration.credential_references:
        return
    await session.execute(
        insert(SessionCredentialRow)
        .values(
            [
                dict(
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    thread_id=thread_id,
                    environment_id=environment_id,
                    binding_name=binding_name,
                    session_version=version,
                    env=binding.env,
                    credential_id=binding.credential_id,
                    inject_hosts=list(binding.inject_hosts),
                )
                for binding in configuration.credential_references
            ]
        )
        .on_conflict_do_nothing()
    )


def selection(thread_id: str, key: tuple[str, str], version: int):
    return (
        SessionCredentialRow.thread_id == thread_id,
        SessionCredentialRow.environment_id == key[0],
        SessionCredentialRow.binding_name == key[1],
        SessionCredentialRow.session_version == version,
    )


async def reset(session: AsyncSession, thread_id: str, key: tuple[str, str], version: int) -> None:
    await session.execute(
        update(SessionCredentialRow)
        .where(*selection(thread_id, key, version))
        .values(
            applied_credential_version=None,
            applied_session_id=None,
            applied_device_generation=None,
            applied_at=None,
        )
    )


async def retire(session: AsyncSession, thread_id: str, key: tuple[str, str], version: int) -> None:
    await session.execute(delete(SessionCredentialRow).where(*selection(thread_id, key, version)))


async def discard_unstarted(
    session: AsyncSession, thread_id: str, key: tuple[str, str], retained_version: int | None
) -> None:
    """Before admitting a replacement, keep only references to its still-existing predecessor."""
    query = delete(SessionCredentialRow).where(
        SessionCredentialRow.thread_id == thread_id,
        SessionCredentialRow.environment_id == key[0],
        SessionCredentialRow.binding_name == key[1],
    )
    if retained_version is not None:
        query = query.where(SessionCredentialRow.session_version != retained_version)
    await session.execute(query)


async def applied(
    session: AsyncSession,
    thread_id: str,
    key: tuple[str, str],
    version: int,
    snapshot: Snapshot,
    descriptor: SessionDescriptor,
) -> None:
    versions = {credential.id: credential.version for credential in snapshot.credentials.values()}
    if versions:
        await session.execute(
            update(SessionCredentialRow)
            .where(
                *selection(thread_id, key, version),
                SessionCredentialRow.credential_id.in_(versions),
            )
            .values(
                applied_credential_version=case(versions, value=SessionCredentialRow.credential_id),
                applied_session_id=descriptor.session_id,
                applied_device_generation=Decimal(descriptor.generation),
                applied_at=datetime.now(UTC),
            )
        )
