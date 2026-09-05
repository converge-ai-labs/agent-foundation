"""Demand-driven OAuth refresh with one fenced owner per credential generation."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.management import canonical_json
from a13n_service.credentials import CredentialSnapshot
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .management import require_connection
from .models import MCPConnectionRecord
from .oauth_bundles import decode_oauth_bundle, optional_expiration, with_expiration
from .oauth_client import MCPOAuthClient


@dataclass(frozen=True, slots=True)
class _Claim:
    connection_id: str
    credential_generation: int
    generation: int
    credential: CredentialSnapshot


class OAuthCredentialRefresh:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        oauth: MCPOAuthClient,
        protector: SecretProtector,
        *,
        instance_id: str,
        lease_seconds: float = 60,
        skew_seconds: float = 60,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._oauth = oauth
        self._protector = protector
        self._instance_id = instance_id
        self._lease_seconds = lease_seconds
        self._skew_seconds = skew_seconds
        self._clock = clock

    async def ensure_current(self, connection_id: str) -> bool:
        claim = await self._claim(connection_id)
        if isinstance(claim, bool):
            return claim
        try:
            bundle = decode_oauth_bundle(claim.credential.decrypt(self._protector))
            candidate = with_expiration(await self._oauth.refresh(dict(bundle)), self._clock())
        except Exception:
            # A token may have rotated before the reply was lost. Never retry the
            # previous refresh token after any uncertain dispatched exchange.
            async with transaction(self._sessions) as session:
                connection = await require_connection(session, connection_id, lock=True, include_deleted=True)
                if self._owns(connection, claim):
                    _reauthorize(connection, now=self._clock())
            return False
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True, include_deleted=True)
            if (
                not self._owns(connection, claim)
                or not await _workspace_active(session, connection)
                or connection.refresh_claim_expires_at is None
                or assume_utc(connection.refresh_claim_expires_at) <= self._clock()
            ):
                return False
            connection.replace_credential(canonical_json(candidate), self._protector)
            connection.refresh_claim_owner = None
            connection.refresh_claim_expires_at = None
            connection.updated_at = self._clock()
        return True

    async def _claim(self, connection_id: str) -> _Claim | bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            if not await _workspace_active(session, connection):
                return False
            if connection.auth_mode != "oauth":
                return True
            if connection.status != "ready" or connection.ciphertext is None:
                return False
            if connection.refresh_claim_owner is not None:
                if (
                    connection.refresh_claim_expires_at is None
                    or assume_utc(connection.refresh_claim_expires_at) <= now
                ):
                    _reauthorize(connection, now=now)
                return False
            credential = connection.credential_snapshot()
            try:
                bundle = decode_oauth_bundle(credential.decrypt(self._protector))
                expires_at = optional_expiration(bundle.get("expires_at"))
                if expires_at is None or expires_at > now + timedelta(seconds=self._skew_seconds):
                    return True
                if not bundle.get("refresh_token"):
                    _reauthorize(connection, now=now)
                    return False
            except (ValueError, SecretProtectionError):
                _reauthorize(connection, now=now)
                return False
            connection.refresh_claim_generation += 1
            connection.refresh_claim_owner = self._instance_id
            connection.refresh_claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            return _Claim(
                connection.id, connection.credential_generation, connection.refresh_claim_generation, credential
            )

    def _owns(self, connection: MCPConnectionRecord, claim: _Claim) -> bool:
        return (
            connection.deleted_at is None
            and connection.status == "ready"
            and connection.auth_mode == "oauth"
            and connection.credential_generation == claim.credential_generation
            and connection.refresh_claim_generation == claim.generation
            and connection.refresh_claim_owner == self._instance_id
        )


def _reauthorize(connection: MCPConnectionRecord, *, now) -> None:
    connection.status = "action_required"
    connection.status_reason = "reauthorization_required"
    connection.refresh_claim_generation += 1
    connection.refresh_claim_owner = None
    connection.refresh_claim_expires_at = None
    connection.updated_at = now


async def _workspace_active(session: AsyncSession, connection: MCPConnectionRecord) -> bool:
    return (
        await session.scalar(
            select(WorkspaceRecord.id).where(
                WorkspaceRecord.id == connection.workspace_id,
                WorkspaceRecord.organization_id == connection.organization_id,
                WorkspaceRecord.deleted_at.is_(None),
            )
        )
        is not None
    )
