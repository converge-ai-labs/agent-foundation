"""Durable envd pairing, Workspace approval, and irreversible connection revocation."""

from __future__ import annotations

from urllib.parse import quote

from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_harness.providers.environment.remote_envd.pairing import (
    MAX_PENDING_PAIRINGS,
    PairingApproved,
    PairingChallenge,
    PairingPending,
    PairingRequest,
    PairingResponse,
    PendingPairing,
    credential_digest,
    pairing_id,
)
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, next_updated_at, utc_now

from .access import authorize_environment_resource, authorize_environment_workspace
from .domain import Environment, RegisterEnvironmentRequest
from .errors import EnvironmentManagementError, environment_not_found, invalid_environment, is_target_identity_conflict
from .models import DevicePairingRecord, EnvironmentProviderRecord, EnvironmentRecord
from .service import EnvironmentService
from .websocket.coordination import ConnectionCoordination, CoordinationError


def pairing_denied() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "device_pairing_denied", "Device pairing was rejected or revoked.", category=ErrorCategory.forbidden
    )


class DevicePairingService:
    def __init__(
        self, environments: EnvironmentService, coordination: ConnectionCoordination, *, public_origin: str
    ) -> None:
        self._environments = environments
        self._sessions = environments.sessions
        self._coordination = coordination
        self._public_origin = public_origin

    async def pair(self, credential: str, request: PairingRequest) -> PairingResponse:
        digest = credential_digest(credential)
        async with transaction(self._sessions) as session:
            # Serialize only short registration writes: bounds and first approval
            # must hold across Control replicas, not just within one process.
            await self._lock(session)
            registered = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.device_credential_digest == digest)
            )
            if registered is not None:
                if registered.device_revoked_at is not None:
                    raise pairing_denied()
                if registered.to_resource().device_id != request.device_id:
                    raise invalid_environment("Pairing credential belongs to another Device")
                return PairingApproved(
                    resource_id=registered.id,
                    websocket_url=f"{self._public_origin}/api/v1/environments/{quote(registered.id, safe='')}/connect",
                )
            now = utc_now()
            await session.execute(delete(DevicePairingRecord).where(DevicePairingRecord.expires_at <= now))
            row = await session.get(DevicePairingRecord, pairing_id(digest))
            if row is None:
                count = await session.scalar(select(func.count()).select_from(DevicePairingRecord))
                if count is not None and count >= MAX_PENDING_PAIRINGS:
                    raise EnvironmentManagementError(
                        "device_pairing_capacity",
                        "Too many pending Device pairings. Try again later.",
                        category=ErrorCategory.rate_limited,
                    )
                pending = PendingPairing.create(request, digest, now=now)
                row = DevicePairingRecord(
                    id=pending.challenge.pairing_id,
                    credential_digest=digest,
                    device_id=request.device_id,
                    name=request.name,
                    expires_at=pending.challenge.expires_at,
                    rejected=False,
                )
                session.add(row)
            elif row.rejected:
                raise pairing_denied()
            elif row.device_id != request.device_id or row.name != request.name or row.credential_digest != digest:
                raise invalid_environment("Pending pairing details cannot change")
            return PairingPending(
                challenge=self._challenge(row),
                approval_url=f"/?envd_pairing={row.id}",
            )

    async def inspect(self, actor: AuthenticatedActor, workspace_id: str, pairing: str) -> PairingChallenge:
        # There is no public/global pending list. The operator brings the
        # unguessable pairing link from their terminal into an authorized Workspace.
        async with short_session(self._sessions) as session:
            await authorize_environment_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_manage
            )
            return self._challenge(await self._pending(session, pairing))

    async def approve(self, actor: AuthenticatedActor, workspace_id: str, pairing: str) -> Environment:
        try:
            async with transaction(self._sessions) as session:
                scope = await authorize_environment_workspace(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_manage
                )
                await self._lock(session)
                # Digest-derived pairing IDs also reconcile lost approval replies
                # after the expiring request has been removed.
                registered = (
                    await session.scalar(
                        select(EnvironmentRecord).where(
                            func.substr(EnvironmentRecord.device_credential_digest, 1, 24) == pairing[5:]
                        )
                    )
                    if len(pairing) == 29 and pairing.startswith("pair-")
                    else None
                )
                if registered is not None:
                    if registered.workspace_id != workspace_id:
                        raise environment_not_found()
                    if registered.device_revoked_at is not None:
                        raise pairing_denied()
                    return registered.to_resource()
                pending = await self._pending(session, pairing)
                provider = await session.scalar(
                    select(EnvironmentProviderRecord)
                    .where(
                        EnvironmentProviderRecord.workspace_id == workspace_id,
                        EnvironmentProviderRecord.type == WEBSOCKET_PROVIDER_KEY,
                        EnvironmentProviderRecord.enabled.is_(True),
                    )
                    .order_by(EnvironmentProviderRecord.id)
                    .limit(1)
                )
                now = utc_now()
                if provider is None:
                    implementation = self._environments.catalog.require(WEBSOCKET_PROVIDER_KEY)
                    provider = EnvironmentProviderRecord(
                        id=new_object_id("envp"),
                        organization_id=scope.organization_id,
                        workspace_id=workspace_id,
                        type=WEBSOCKET_PROVIDER_KEY,
                        name="Connected devices",
                        configuration=implementation.configuration_model.model_validate({}).model_dump(mode="json"),
                        configuration_source="user",
                        enabled=True,
                        credential_generation=0,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(provider)
                    await session.flush()
                row = await self._environments.register_external(
                    session,
                    actor,
                    workspace_id,
                    RegisterEnvironmentRequest(
                        provider_id=provider.id, name=pending.name, device_id=pending.device_id, configuration={}
                    ),
                    now,
                )
                row.device_credential_digest = pending.credential_digest
                await session.delete(pending)
                await session.flush()
                return row.to_resource()
        except IntegrityError as error:
            if is_target_identity_conflict(error):
                raise EnvironmentManagementError(
                    "environment_target_conflict",
                    "This Device already has an Environment owner. Reuse its saved connection or start a new envd instance.",
                    category=ErrorCategory.conflict,
                ) from error
            raise

    async def reject(self, actor: AuthenticatedActor, workspace_id: str, pairing: str) -> None:
        async with transaction(self._sessions) as session:
            await authorize_environment_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_manage
            )
            await self._lock(session)
            row = await self._pending(session, pairing)
            row.rejected = True

    async def revoke(self, actor: AuthenticatedActor, environment_id: str) -> Environment:
        async with transaction(self._sessions) as session:
            row = await self._environments.require_environment(session, actor, environment_id, lock=True)
            await authorize_environment_resource(
                session,
                actor=actor,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                action=WorkspaceAction.environment_manage,
                manage=True,
            )
            if row.device_credential_digest is None:
                raise invalid_environment("Environment is not a paired Device")
            if row.device_revoked_at is None:
                now = utc_now()
                row.device_revoked_at = now
                row.updated_at = next_updated_at(row.updated_at, now)
                row.status = "unavailable"
            result = row.to_resource()
        # Durable revocation is authoritative even when Redis is unavailable.
        # The owner revalidates it on bounded renewal and fences on failure.
        try:
            await self._coordination.revoke(result.organization_id, result.id)
        except CoordinationError:
            pass
        return result

    @staticmethod
    async def _lock(session: AsyncSession) -> None:
        await session.execute(text("SELECT pg_advisory_xact_lock(134644, 2)"))

    @staticmethod
    async def _pending(session: AsyncSession, pairing: str) -> DevicePairingRecord:
        row = await session.get(DevicePairingRecord, pairing)
        if row is None or row.rejected or assume_utc(row.expires_at) <= utc_now():
            raise environment_not_found()
        return row

    @staticmethod
    def _challenge(row: DevicePairingRecord) -> PairingChallenge:
        code = row.credential_digest[24:32].upper()
        return PairingChallenge(
            device_id=row.device_id,
            name=row.name,
            pairing_id=row.id,
            verification_code=f"{code[:4]}-{code[4:]}",
            expires_at=assume_utc(row.expires_at),
        )
