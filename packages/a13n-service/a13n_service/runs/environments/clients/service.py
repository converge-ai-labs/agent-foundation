"""Client registration and credential lifecycle, without connecting to a device."""

import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.environments.access import audit, find, writable
from a13n_service.runs.environments.clients.schemas import (
    ClientEnvironment,
    ClientEnvironmentConfiguration,
    Registered,
    Registration,
    StatusChange,
)
from a13n_service.runs.environments.clients.tables import ClientSettingsRow
from a13n_service.runs.environments.schemas import EnvironmentView
from a13n_service.runs.environments.tables import CLIENT_IDENTITY, EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import principal_for, workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.credentials import new_secret


async def settings(session: AsyncSession, environment: EnvironmentRow) -> ClientSettingsRow:
    if environment.kind != "external":
        raise not_found("client_environment", environment.id)
    row = await session.get(ClientSettingsRow, environment.id)
    if row is None:
        raise conflict("environment", environment.id, "client_configuration_missing")
    return row


def view(environment: EnvironmentRow, config: ClientSettingsRow) -> ClientEnvironment:
    return ClientEnvironment(
        environment=EnvironmentView.model_validate(environment),
        configuration=ClientEnvironmentConfiguration.model_validate(config),
        credential_version=config.credential_version,
        credential_present=config.token_hash is not None,
    )


def registered(environment: EnvironmentRow, config: ClientSettingsRow, token: str | None = None) -> Registered:
    return Registered(
        client=view(environment, config),
        token=token,
        connection_path=f"/api/v1/client-environments/{environment.id}/connect",
    )


async def read(runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str) -> ClientEnvironment:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        environment = await find(session, scope, environment_id)
        result = view(environment, await settings(session, environment))
    await runtime.client_connections.observe([result.environment])
    return result


async def register(runtime: Runtime, actor: Principal, workspace_id: str, body: Registration) -> Registered:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        environment = await session.scalar(
            insert(EnvironmentRow)
            .values(
                id=new_object_id("env"),
                kind="external",
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                owner_principal_id=actor.id,
                device_id=body.device_id,
                name=body.name,
                status="ready",
                created_by_id=actor.id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    EnvironmentRow.workspace_id,
                    EnvironmentRow.owner_principal_id,
                    EnvironmentRow.device_id,
                ],
                index_where=CLIENT_IDENTITY,
            )
            .returning(EnvironmentRow)
        )
        if environment is None:
            existing = (
                await session.execute(
                    select(EnvironmentRow, ClientSettingsRow)
                    .join(ClientSettingsRow, ClientSettingsRow.environment_id == EnvironmentRow.id)
                    .where(
                        CLIENT_IDENTITY,
                        EnvironmentRow.workspace_id == scope.workspace_id,
                        EnvironmentRow.owner_principal_id == actor.id,
                        EnvironmentRow.device_id == body.device_id,
                    )
                )
            ).one_or_none()
            if existing is None:
                raise conflict("workspace", workspace_id, "client_registration_changed")
            return registered(existing[0], existing[1])
        token = new_secret()
        config = ClientSettingsRow(
            environment_id=environment.id,
            token_hash=secret_hash(token),
            credential_version=1,
            default_working_directory=body.default_working_directory,
            permission_ceiling=body.permission_ceiling.model_dump(mode="json"),
            declared_capabilities=body.declared_capabilities.model_dump(mode="json")
            if body.declared_capabilities is not None
            else None,
            description=body.description,
        )
        session.add(config)
        audit(session, actor, environment, "client.register")
        await session.flush()
        return registered(environment, config, token)


async def change_status(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    body: StatusChange,
    *,
    if_match: str | None,
) -> ClientEnvironment:
    async with transaction(runtime.storage) as session:
        environment = await writable(session, actor, workspace_id, environment_id, if_match)
        config = await settings(session, environment)
        if environment.status == "deleted":
            raise conflict("environment", environment.id, "environment_deleted")
        if environment.status != body.status:
            environment.status = body.status
            # Fence connections even if disable/re-enable both happen before a
            # Control's next check. The retained token can authenticate a new carrier.
            config.credential_version += 1
            audit(session, actor, environment, f"client.{body.status}")
            await session.flush()
        return view(environment, config)


async def credential(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    *,
    rotate: bool,
    if_match: str | None,
) -> Registered:
    async with transaction(runtime.storage) as session:
        environment = await writable(session, actor, workspace_id, environment_id, if_match)
        config = await settings(session, environment)
        if environment.status == "deleted":
            raise conflict("environment", environment.id, "environment_deleted")
        token = new_secret() if rotate else None
        config.token_hash = secret_hash(token) if token is not None else None
        config.credential_version += 1
        environment.updated_at = await now(session)
        audit(session, actor, environment, "client.rotate" if rotate else "client.revoke")
        await session.flush()
        return registered(environment, config, token)


@dataclass(frozen=True, slots=True)
class Attachment:
    environment_id: str
    device_id: str
    credential_version: int


async def authenticate(runtime: Runtime, environment_id: str, token: str) -> Attachment:
    """Detached connection identity; never hold this read across a WebSocket."""
    async with short_session(runtime.storage) as session:
        result = (
            await session.execute(
                select(
                    EnvironmentRow.device_id,
                    EnvironmentRow.owner_principal_id,
                    EnvironmentRow.workspace_id,
                    ClientSettingsRow.token_hash,
                    ClientSettingsRow.credential_version,
                )
                .join(ClientSettingsRow, ClientSettingsRow.environment_id == EnvironmentRow.id)
                .where(
                    EnvironmentRow.id == environment_id,
                    EnvironmentRow.kind == "external",
                    EnvironmentRow.status == "ready",
                )
            )
        ).one_or_none()
        if (
            result is None
            or result.token_hash is None
            or not secrets.compare_digest(result.token_hash, secret_hash(token))
        ):
            raise ServiceError("unauthenticated", "Invalid client environment credential")
        owner = await principal_for(session, runtime.access, result.owner_principal_id)
        await workspace_scope(session, owner, result.workspace_id, "write")
        return Attachment(environment_id, result.device_id, result.credential_version)


async def validate_attachment(runtime: Runtime, attachment: Attachment) -> None:
    """A live carrier retains admission only while its original registration is current."""
    async with short_session(runtime.storage) as session:
        result = (
            await session.execute(
                select(EnvironmentRow.owner_principal_id, EnvironmentRow.workspace_id)
                .join(ClientSettingsRow, ClientSettingsRow.environment_id == EnvironmentRow.id)
                .where(
                    EnvironmentRow.id == attachment.environment_id,
                    EnvironmentRow.kind == "external",
                    EnvironmentRow.status == "ready",
                    EnvironmentRow.device_id == attachment.device_id,
                    ClientSettingsRow.credential_version == attachment.credential_version,
                    ClientSettingsRow.token_hash.is_not(None),
                )
            )
        ).one_or_none()
        if result is None:
            raise ServiceError("unauthenticated", "Client environment connection is no longer authorized")
        owner = await principal_for(session, runtime.access, result.owner_principal_id)
        await workspace_scope(session, owner, result.workspace_id, "write")
