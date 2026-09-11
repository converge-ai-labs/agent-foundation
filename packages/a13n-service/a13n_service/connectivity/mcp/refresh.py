"""Demand-driven OAuth refresh with one fenced owner per credential generation."""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from random import random
from time import monotonic

import httpx2
from anyio import sleep
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.credentials import CredentialSnapshot
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .credentials import decode_request_headers
from .errors import MCPConnectionError
from .management import require_connection
from .models import MCPConnectionOAuthClientRecord, MCPConnectionRecord
from .oauth_bundles import decode_oauth_bundle, optional_expiration, with_expiration
from .oauth_client import MCPOAuthClient, OAuthClientContext
from .oauth_configuration import client_refresh_context


@dataclass(frozen=True, slots=True)
class _Claim:
    connection_id: str
    credential_generation: int
    generation: int
    credential: CredentialSnapshot
    client: OAuthClientContext = field(repr=False)


@dataclass(frozen=True, slots=True)
class CurrentConnection:
    endpoint: str
    version: int
    credential_generation: int
    headers: dict[str, str] = field(repr=False)


class _RefreshState(Enum):
    current = "current"
    unavailable = "unavailable"
    refreshing = "refreshing"


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
        wait_seconds: float = 30,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._oauth = oauth
        self._protector = protector
        self._instance_id = instance_id
        self._lease_seconds = lease_seconds
        self._skew_seconds = skew_seconds
        self._clock = clock
        self._wait_seconds = wait_seconds

    async def current(self, connection_id: str) -> CurrentConnection:
        if not await self.ensure_current(connection_id):
            raise MCPConnectionError(
                "mcp_credentials_unavailable", "MCP credentials are unavailable.", category=ErrorCategory.unavailable
            )
        async with short_session(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            if connection.status not in {"ready", "pending"} or not await _workspace_active(session, connection):
                raise MCPConnectionError(
                    "connection_unavailable", "MCPConnection is unavailable.", category=ErrorCategory.conflict
                )
            value = (
                connection.credential_snapshot().decrypt(self._protector) if connection.auth_mode != "none" else None
            )
            if connection.auth_mode == "oauth":
                assert value is not None
                expires_at = optional_expiration(decode_oauth_bundle(value).get("expires_at"))
                if expires_at is not None and expires_at <= self._clock():
                    raise MCPConnectionError(
                        "mcp_credentials_expired", "MCP credentials expired.", category=ErrorCategory.unavailable
                    )
            return CurrentConnection(
                connection.endpoint_url,
                connection.version,
                connection.credential_generation,
                decode_request_headers(value) if value is not None else {},
            )

    async def ensure_current(self, connection_id: str) -> bool:
        deadline = monotonic() + self._wait_seconds
        claim = await self._claim(connection_id)
        delay = 0.1
        while claim is _RefreshState.refreshing:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            # A competing owner may rotate the token. Wait without retaining a
            # session, then read its committed result before claiming any work.
            await sleep(min(delay * (1 + random() * 0.25), remaining))
            delay = min(delay * 2, 1.0)
            claim = await self._claim(connection_id)
        if isinstance(claim, _RefreshState):
            return claim is _RefreshState.current
        try:
            bundle = decode_oauth_bundle(claim.credential.decrypt(self._protector))
            candidate = with_expiration(await self._oauth.refresh(dict(bundle), claim.client), self._clock())
        except Exception as error:
            # A token may have rotated before the reply was lost. Never retry the
            # previous refresh token after any uncertain dispatched exchange.
            async with transaction(self._sessions) as session:
                connection = await require_connection(session, connection_id, lock=True, include_deleted=True)
                if self._owns(connection, claim):
                    if isinstance(error, (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)):
                        connection.refresh_claim_owner = None
                        connection.refresh_claim_expires_at = None
                    else:
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

    async def _claim(self, connection_id: str) -> _Claim | _RefreshState:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            if not await _workspace_active(session, connection):
                return _RefreshState.unavailable
            if connection.auth_mode != "oauth":
                return _RefreshState.current
            if connection.status not in {"ready", "pending"} or connection.ciphertext is None:
                return _RefreshState.unavailable
            if connection.refresh_claim_owner is not None:
                if (
                    connection.refresh_claim_expires_at is None
                    or assume_utc(connection.refresh_claim_expires_at) <= now
                ):
                    _reauthorize(connection, now=now)
                    return _RefreshState.unavailable
                return _RefreshState.refreshing
            credential = connection.credential_snapshot()
            try:
                bundle = decode_oauth_bundle(credential.decrypt(self._protector))
                expires_at = optional_expiration(bundle.get("expires_at"))
                if expires_at is None or expires_at > now + timedelta(seconds=self._skew_seconds):
                    return _RefreshState.current
                client = _refresh_client(
                    await session.get(MCPConnectionOAuthClientRecord, connection_id),
                    bundle,
                    self._protector,
                )
            except (ValueError, SecretProtectionError):
                _reauthorize(connection, now=now)
                return _RefreshState.unavailable
            # SQLite ignores FOR UPDATE. Compare the observed generations in
            # the write itself so concurrent readers cannot both claim a token.
            generation = await session.scalar(
                update(MCPConnectionRecord)
                .where(
                    MCPConnectionRecord.id == connection.id,
                    MCPConnectionRecord.version == connection.version,
                    MCPConnectionRecord.status == connection.status,
                    MCPConnectionRecord.auth_mode == "oauth",
                    MCPConnectionRecord.deleted_at.is_(None),
                    MCPConnectionRecord.credential_generation == credential.generation,
                    MCPConnectionRecord.refresh_claim_generation == connection.refresh_claim_generation,
                    MCPConnectionRecord.refresh_claim_owner.is_(None),
                )
                .values(
                    refresh_claim_generation=connection.refresh_claim_generation + 1,
                    refresh_claim_owner=self._instance_id,
                    refresh_claim_expires_at=now + timedelta(seconds=self._lease_seconds),
                )
                .returning(MCPConnectionRecord.refresh_claim_generation)
                .execution_options(synchronize_session=False)
            )
            if generation is None:
                return _RefreshState.refreshing
            return _Claim(connection.id, credential.generation, generation, credential, client)

    def _owns(self, connection: MCPConnectionRecord, claim: _Claim) -> bool:
        return (
            connection.deleted_at is None
            and connection.status in {"ready", "pending"}
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


def _refresh_client(
    record: MCPConnectionOAuthClientRecord | None,
    bundle: JsonObject,
    protector: SecretProtector,
) -> OAuthClientContext:
    if record is None:
        raise ValueError("missing OAuth client")
    client = client_refresh_context(record, protector)
    if bundle.get("grant_type") != client.grant_type:
        raise ValueError("OAuth grant mismatch")
    if not bundle.get("refresh_token") and client.grant_type != "client_credentials":
        raise ValueError("missing refresh token")
    return client


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
