"""Durable MCP OAuth authorization, callback, and refresh orchestration."""

from __future__ import annotations

import base64
import hashlib
import secrets as random_secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx2
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json, record_command, replay_command
from a13n_service.credentials import CredentialSnapshot
from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import (
    IdempotencyConflict,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
from a13n_service.iam import AuthenticatedActor, PrincipalType
from a13n_service.ids import new_object_id
from a13n_service.secrets import (
    SecretProtectionError,
    SecretProtector,
)
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import MCPAuthorizationLaunch, MCPClientMetadata, MCPConnection, MCPOAuthClientInput
from .errors import MCPConnectionError
from .management import (
    audit,
    authorize_connection,
    invalidate_refresh_claim,
    map_management_error,
    require_connection,
    require_version,
)
from .models import MCPConnectionOAuthClientRecord, MCPConnectionRecord, MCPOAuthSessionRecord
from .oauth_bundles import (
    decode_oauth_bundle,
    oauth_preparation,
    oauth_setup_bundle,
    required_oauth_string,
    validate_oauth_bundle,
    with_expiration,
)
from .oauth_client import (
    MCPOAuthClient,
    MCPOAuthError,
    OAuthClientContext,
    OAuthPreparation,
    authorization_url,
    oauth_client_metadata,
    oauth_redirect_uri,
)
from .oauth_configuration import OAuthConfiguration, client_refresh_context, configured_client, store_client
from .service import ConnectionDiscovery


@dataclass(frozen=True, slots=True)
class OAuthSource:
    connection_id: str
    organization_id: str
    workspace_id: str
    endpoint_url: str
    version: int
    client: MCPOAuthClientInput | None


@dataclass(frozen=True, slots=True)
class OAuthSessionSnapshot:
    id: str
    mcp_connection_id: str
    expires_at: datetime
    credential: CredentialSnapshot

    @classmethod
    def from_record(cls, record: MCPOAuthSessionRecord) -> OAuthSessionSnapshot:
        return cls(record.id, record.mcp_connection_id, assume_utc(record.expires_at), record.credential_snapshot())


@dataclass(frozen=True, slots=True)
class MachineOAuthSource:
    connection_id: str
    endpoint_url: str
    version: int
    client_generation: int
    client: OAuthClientContext


class MCPOAuthService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        oauth: MCPOAuthClient,
        protector: SecretProtector,
        discovery: ConnectionDiscovery,
        *,
        public_origin: str | None,
        client_name: str,
        instance_id: str,
        setup_ttl_seconds: int = 600,
        claim_lease_seconds: int = 60,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._oauth = oauth
        self._protector = protector
        self._discovery = discovery
        self._public_origin = public_origin.rstrip("/") if public_origin is not None else None
        self._client_name = client_name
        self._instance_id = instance_id
        self._setup_ttl_seconds = setup_ttl_seconds
        self._claim_lease_seconds = claim_lease_seconds
        self._clock = clock

    def client_metadata(self, issuer_key: str) -> MCPClientMetadata:
        return oauth_client_metadata(self._require_origin(), issuer_key, self._client_name)

    @property
    def configuration(self) -> OAuthConfiguration:
        return OAuthConfiguration(
            self._sessions, self._oauth, self._protector, public_origin=self._public_origin, clock=self._clock
        )

    def _require_origin(self) -> str:
        if self._public_origin is None:
            raise MCPConnectionError(
                "oauth_unavailable",
                "Interactive OAuth requires a configured public origin.",
                category=ErrorCategory.unavailable,
            )

        return self._public_origin

    async def authorize(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> MCPAuthorizationLaunch:
        _require_user(actor)
        self._require_origin()
        key_digest = _idempotency_digest(idempotency_key)
        request_fingerprint = digest_request({"expected_version": expected_version})
        source = await self._authorize_source(
            actor=actor,
            connection_id=connection_id,
            expected_version=expected_version,
            key_digest=key_digest,
            request_fingerprint=request_fingerprint,
        )
        if isinstance(source, OAuthSessionSnapshot):
            return await self._launch_from_session(source)
        try:
            preparation = await self._oauth.prepare(
                source.endpoint_url,
                public_origin=self._require_origin(),
                client_name=self._client_name,
                client=source.client,
            )
        except MCPOAuthError as error:
            incompatible = error.code not in {"client_registration_failed", "metadata_unavailable"}
            if incompatible:
                await self._mark_incompatible(source.connection_id, source.version)
            raise MCPConnectionError(
                "mcp_oauth_incompatible" if incompatible else "mcp_oauth_unavailable",
                (
                    "Remote MCP OAuth setup is incompatible."
                    if incompatible
                    else "Remote MCP OAuth setup is temporarily unavailable."
                ),
                category=ErrorCategory.conflict if incompatible else ErrorCategory.unavailable,
            ) from error
        except httpx2.HTTPError as error:
            raise MCPConnectionError(
                "mcp_oauth_unavailable",
                "Remote MCP OAuth setup is temporarily unavailable.",
                category=ErrorCategory.unavailable,
            ) from error
        try:
            return await self._create_session(
                actor=actor,
                source=source,
                preparation=preparation,
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
            )
        except Exception:
            try:
                await self._oauth.cleanup_registration_bundle(asdict(preparation))
            except (MCPOAuthError, httpx2.HTTPError):
                pass
            raise

    async def authenticate_client_credentials(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> MCPConnection:
        key_digest = _idempotency_digest(idempotency_key)
        fingerprint = digest_request({"expected_version": expected_version})
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            try:
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=connection.workspace_id,
                    operation="mcp_connection.client_credentials",
                    scope_id=connection.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=fingerprint,
                    now=self._clock(),
                )
            except IdempotencyConflict as error:
                raise map_management_error(error) from error
            if replay is not None:
                return replay.restore(MCPConnection)
            require_version(connection.version, expected_version)
            if connection.status == "disabled":
                raise MCPConnectionError(
                    "connection_disabled", "MCPConnection is disabled.", category=ErrorCategory.conflict
                )
            client_record = await session.get(MCPConnectionOAuthClientRecord, connection.id)
            if client_record is None:
                raise MCPConnectionError(
                    "oauth_client_required", "Client credentials are not configured.", category=ErrorCategory.conflict
                )
            client = client_refresh_context(client_record, self._protector)
            if client.grant_type != "client_credentials" or client.client_secret is None:
                raise MCPConnectionError(
                    "invalid_oauth_grant",
                    "This MCPConnection does not use client credentials.",
                    category=ErrorCategory.conflict,
                )
            source = MachineOAuthSource(
                connection.id,
                connection.endpoint_url,
                connection.version,
                client_record.credential_generation,
                client,
            )
        credential = await self._acquire_machine_credential(source)
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            client_record = await session.get(MCPConnectionOAuthClientRecord, connection_id)
            if (
                not _source_matches(connection, source)
                or client_record is None
                or client_record.credential_generation != source.client_generation
            ):
                raise MCPConnectionError(
                    "version_conflict",
                    "MCPConnection changed during machine authorization.",
                    category=ErrorCategory.conflict,
                )
            connection.replace_credential(canonical_json(validate_oauth_bundle(credential)), self._protector)
            connection.status = "pending"
            connection.status_reason = None
            connection.version += 1
            connection.updated_at = now
            invalidate_refresh_claim(connection, now=now)
            resource = connection.to_resource()
            command_id = record_command(
                session,
                actor=actor,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                operation="mcp_connection.client_credentials",
                scope_id=connection.id,
                idempotency_key_digest=key_digest,
                fingerprint=fingerprint,
                resource_type="mcp_connection",
                resource_id=connection.id,
                result_version=connection.version,
                now=now,
                resource=resource,
            ).id
            session.add(audit(actor, connection, action="mcp_connection.client_credentials", now=now))
        try:
            return (await self._discovery.discover(connection_id, actor=actor, command_id=command_id)).connection
        except MCPConnectionError as error:
            if error.code != "mcp_discovery_unavailable":
                raise
            return resource

    async def _acquire_machine_credential(self, source: MachineOAuthSource) -> JsonObject:
        try:
            credential = await self._oauth.acquire_client_credentials(source.client)
            return with_expiration(credential, self._clock())
        except MCPOAuthError as error:
            if error.action_required:
                raise MCPConnectionError(
                    "mcp_oauth_rejected",
                    "Remote MCP machine credentials were rejected.",
                    category=ErrorCategory.conflict,
                ) from error
            raise MCPConnectionError(
                "mcp_oauth_unavailable",
                "Remote MCP machine authorization could not be completed.",
                category=ErrorCategory.unavailable,
            ) from error
        except httpx2.HTTPError as error:
            raise MCPConnectionError(
                "mcp_oauth_unavailable",
                "Remote MCP machine authorization could not be completed.",
                category=ErrorCategory.unavailable,
            ) from error

    async def receive_callback(self, *, callback_key: str, state: str, code: str, issuer: str | None) -> str:
        """Capture the provider response before an authenticated browser completes it."""
        now = self._clock()
        receipt = random_secrets.token_urlsafe(32)
        async with transaction(self._sessions) as session:
            connection_id = await session.scalar(
                select(MCPOAuthSessionRecord.mcp_connection_id).where(
                    MCPOAuthSessionRecord.state_digest == _digest(state)
                )
            )
            if connection_id is None:
                raise MCPConnectionError(
                    "invalid_oauth_state", "OAuth callback state is invalid.", category=ErrorCategory.invalid_request
                )
            connection = await require_connection(session, connection_id, lock=True)
            setup_record = await session.scalar(
                select(MCPOAuthSessionRecord)
                .where(MCPOAuthSessionRecord.state_digest == _digest(state))
                .with_for_update()
            )
            if (
                setup_record is None
                or setup_record.status != "pending"
                or assume_utc(setup_record.expires_at) <= now
                or connection.status != "pending"
                or connection.version != setup_record.connection_version
            ):
                raise MCPConnectionError(
                    "oauth_session_unavailable",
                    "OAuth authorization session is unavailable.",
                    category=ErrorCategory.conflict,
                )
            setup = self._resolve_setup(OAuthSessionSnapshot.from_record(setup_record))
            preparation = oauth_preparation(setup)
            if preparation.redirect_uri != oauth_redirect_uri(self._require_origin(), callback_key):
                raise MCPConnectionError(
                    "oauth_callback_mismatch",
                    "OAuth callback address is invalid.",
                    category=ErrorCategory.invalid_request,
                )
            if issuer is not None and issuer != preparation.issuer_url:
                raise MCPConnectionError(
                    "oauth_issuer_mismatch", "OAuth callback issuer is invalid.", category=ErrorCategory.invalid_request
                )
            setup["code"] = code
            setup["receipt_digest"] = _digest(receipt)
            setup_record.replace_credential(canonical_json(setup), self._protector)
            setup_record.status = "received"
            setup_record.updated_at = now
        query = urlencode({"state": state, "receipt": receipt})
        return f"{self._public_origin}/mcp-setup/callback#{query}"

    async def callback(
        self,
        *,
        actor: AuthenticatedActor,
        state: str,
        receipt: str,
    ) -> MCPConnection:
        _require_user(actor)
        source = await self._reserve_callback(actor=actor, state=state, receipt=receipt)
        try:
            setup = self._resolve_setup(source.session)
            preparation = oauth_preparation(setup)
            credential = await self._oauth.exchange_code(
                preparation,
                code=required_oauth_string(setup, "code"),
                verifier=required_oauth_string(setup, "verifier"),
            )
            credential = with_expiration(credential, self._clock())
            await self._complete_callback(actor, source, credential)
        except (SecretProtectionError, MCPOAuthError, ValueError) as error:
            await self._callback_failed(source, error)
            unavailable = isinstance(error, MCPOAuthError) and error.code == "token_exchange_unavailable"
            raise MCPConnectionError(
                "mcp_oauth_unavailable" if unavailable else "mcp_oauth_callback_failed",
                (
                    "Remote MCP authorization is temporarily unavailable."
                    if unavailable
                    else "Remote MCP authorization could not be completed."
                ),
                category=ErrorCategory.unavailable if unavailable else ErrorCategory.conflict,
            ) from error
        except httpx2.HTTPError as error:
            await self._callback_failed(source, error)
            raise MCPConnectionError(
                "mcp_oauth_unavailable",
                "Remote MCP authorization is temporarily unavailable.",
                category=ErrorCategory.unavailable,
            ) from error
        try:
            await self._discovery.discover(source.connection_id, actor=actor)
        except MCPConnectionError as error:
            if error.code != "mcp_discovery_unavailable":
                raise
            # Authorization is already durable. Verification is safe to retry
            # without asking the user to grant consent a second time.
            pass
        async with transaction(self._sessions) as session:
            record = await require_connection(session, source.connection_id)
            await authorize_connection(session, actor, record, mode="read")
            return record.to_resource()

    async def _authorize_source(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        key_digest: str,
        request_fingerprint: str,
    ) -> OAuthSource | OAuthSessionSnapshot:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            if connection.auth_mode != "oauth":
                raise MCPConnectionError(
                    "invalid_auth_mode",
                    "MCPConnection does not use OAuth.",
                    category=ErrorCategory.conflict,
                )
            try:
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=connection.workspace_id,
                    operation="mcp_connection.authorize",
                    scope_id=connection.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    now=self._clock(),
                )
            except IdempotencyConflict as error:
                raise map_management_error(error) from error
            if replay is not None:
                oauth_session = await session.get(MCPOAuthSessionRecord, replay.resource_id)
                if (
                    oauth_session is None
                    or oauth_session.mcp_connection_id != connection.id
                    or oauth_session.status != "pending"
                ):
                    raise MCPConnectionError(
                        "oauth_session_unavailable",
                        "OAuth authorization session is no longer available.",
                        category=ErrorCategory.conflict,
                    )
                return OAuthSessionSnapshot.from_record(oauth_session)
            require_version(connection.version, expected_version)
            if connection.status == "disabled":
                raise MCPConnectionError(
                    "connection_disabled", "MCPConnection is disabled.", category=ErrorCategory.conflict
                )
            client_record = await session.get(MCPConnectionOAuthClientRecord, connection.id)
            client = configured_client(client_record, self._protector) if client_record is not None else None
            if client is not None and client.grant_type != "authorization_code":
                raise MCPConnectionError(
                    "invalid_oauth_grant",
                    "This MCPConnection uses client credentials.",
                    category=ErrorCategory.conflict,
                )
            return OAuthSource(
                connection_id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                endpoint_url=connection.endpoint_url,
                version=connection.version,
                client=client,
            )

    async def _create_session(
        self,
        *,
        actor: AuthenticatedActor,
        source: OAuthSource,
        preparation: OAuthPreparation,
        key_digest: str,
        request_fingerprint: str,
    ) -> MCPAuthorizationLaunch:
        state = random_secrets.token_urlsafe(32)
        verifier = random_secrets.token_urlsafe(64)
        now = self._clock()
        expires_at = now + timedelta(seconds=self._setup_ttl_seconds)
        setup_value = canonical_json(oauth_setup_bundle(preparation, state=state, verifier=verifier))
        session_id = new_object_id("mos")
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, source.connection_id, lock=True)
            if not _source_matches(connection, source):
                raise MCPConnectionError(
                    "version_conflict",
                    "MCPConnection changed during OAuth discovery.",
                    category=ErrorCategory.conflict,
                )
            await session.execute(
                update(MCPOAuthSessionRecord)
                .where(
                    MCPOAuthSessionRecord.mcp_connection_id == connection.id,
                    MCPOAuthSessionRecord.status.in_(("pending", "received", "exchanging")),
                )
                .values(
                    status="expired",
                    ciphertext=None,
                    nonce=None,
                    encryption_key_id=None,
                    claim_owner=None,
                    claim_expires_at=None,
                    updated_at=now,
                )
            )

            if source.client is None:
                client_record = MCPConnectionOAuthClientRecord(
                    id=connection.id,
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    credential_generation=0,
                )
                store_client(
                    client_record,
                    MCPOAuthClientInput.model_validate(
                        {
                            "issuer_url": preparation.issuer_url,
                            "client_id": preparation.client_id,
                            "client_secret": preparation.client_secret,
                            "token_endpoint_auth_method": preparation.token_endpoint_auth_method,
                        }
                    ),
                    source="dynamic" if preparation.registration_endpoint is not None else "metadata_document",
                    protector=self._protector,
                    resource_url=preparation.resource_url,
                    token_endpoint=preparation.token_endpoint,
                    scope=preparation.scope,
                    registration_access_token=preparation.registration_access_token,
                    registration_client_uri=preparation.registration_client_uri,
                )
                session.add(client_record)

            oauth_session = MCPOAuthSessionRecord(
                id=session_id,
                organization_id=source.organization_id,
                workspace_id=source.workspace_id,
                mcp_connection_id=source.connection_id,
                initiating_user_id=actor.principal.principal_id,
                connection_version=connection.version + 1,
                state_digest=_digest(state),
                credential_generation=0,
                status="pending",
                claim_generation=0,
                claim_owner=None,
                claim_expires_at=None,
                expires_at=expires_at,
                consumed_at=None,
                last_error_code=None,
                created_at=now,
                updated_at=now,
            )
            oauth_session.replace_credential(setup_value, self._protector)
            session.add(oauth_session)
            connection.status = "pending"
            connection.status_reason = None
            connection.version += 1
            connection.updated_at = now
            invalidate_refresh_claim(connection, now=now)
            record_command(
                session,
                actor=actor,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                operation="mcp_connection.authorize",
                scope_id=connection.id,
                idempotency_key_digest=key_digest,
                fingerprint=request_fingerprint,
                resource_type="mcp_oauth_session",
                resource_id=session_id,
                result_version=connection.version,
                now=now,
                resource=None,
            )
            session.add(audit(actor, connection, action="mcp_connection.authorize", now=now))
            await session.flush()
        return MCPAuthorizationLaunch(
            id=session_id,
            authorization_url=authorization_url(
                preparation,
                state=state,
                code_verifier=verifier,
            ),
            expires_at=expires_at,
        )

    async def _launch_from_session(self, oauth_session: OAuthSessionSnapshot) -> MCPAuthorizationLaunch:
        async with transaction(self._sessions) as session:
            await require_connection(session, oauth_session.mcp_connection_id)
        setup = self._resolve_setup(oauth_session)
        preparation = oauth_preparation(setup)
        verifier = required_oauth_string(setup, "verifier")
        return MCPAuthorizationLaunch(
            id=oauth_session.id,
            authorization_url=authorization_url(
                preparation,
                state=required_oauth_string(setup, "state"),
                code_verifier=verifier,
            ),
            expires_at=assume_utc(oauth_session.expires_at),
        )

    async def _reserve_callback(
        self,
        *,
        actor: AuthenticatedActor,
        state: str,
        receipt: str,
    ) -> CallbackSource:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection_id = await session.scalar(
                select(MCPOAuthSessionRecord.mcp_connection_id).where(
                    MCPOAuthSessionRecord.state_digest == _digest(state)
                )
            )
            if connection_id is None:
                raise MCPConnectionError(
                    "invalid_oauth_state", "OAuth callback state is invalid.", category=ErrorCategory.invalid_request
                )
            connection = await require_connection(session, connection_id, lock=True)
            oauth_session = await session.scalar(
                select(MCPOAuthSessionRecord)
                .where(MCPOAuthSessionRecord.state_digest == _digest(state))
                .with_for_update()
            )
            if oauth_session is None or oauth_session.initiating_user_id != actor.principal.principal_id:
                raise MCPConnectionError(
                    "invalid_oauth_state", "OAuth callback state is invalid.", category=ErrorCategory.invalid_request
                )
            await authorize_connection(session, actor, connection, mode="manage")
            if assume_utc(oauth_session.expires_at) <= now:
                oauth_session.status = "expired"
                oauth_session.updated_at = now
                raise MCPConnectionError(
                    "oauth_session_expired", "OAuth authorization session expired.", category=ErrorCategory.conflict
                )
            if oauth_session.status == "completed":
                raise MCPConnectionError(
                    "oauth_state_replayed", "OAuth callback state was already used.", category=ErrorCategory.conflict
                )
            if (
                oauth_session.status != "received"
                or connection.status != "pending"
                or connection.version != oauth_session.connection_version
            ):
                raise MCPConnectionError(
                    "oauth_session_unavailable",
                    "Start a new authorization session; the previous session is unavailable.",
                    category=ErrorCategory.conflict,
                )
            snapshot = OAuthSessionSnapshot.from_record(oauth_session)
            if not random_secrets.compare_digest(
                _digest(receipt), required_oauth_string(self._resolve_setup(snapshot), "receipt_digest")
            ):
                raise MCPConnectionError(
                    "invalid_oauth_receipt",
                    "OAuth callback receipt is invalid.",
                    category=ErrorCategory.invalid_request,
                )
            oauth_session.status = "exchanging"
            oauth_session.claim_generation += 1
            oauth_session.claim_owner = self._instance_id
            oauth_session.claim_expires_at = now + timedelta(seconds=self._claim_lease_seconds)
            oauth_session.updated_at = now
            return CallbackSource(
                connection_id=connection.id,
                connection_version=connection.version,
                credential_generation=connection.credential_generation,
                session=snapshot,
                claim_generation=oauth_session.claim_generation,
                claim_owner=self._instance_id,
            )

    def _resolve_setup(
        self,
        oauth_session: OAuthSessionSnapshot,
    ) -> JsonObject:
        raw = oauth_session.credential.decrypt(self._protector)
        return decode_oauth_bundle(raw)

    async def _complete_callback(
        self,
        actor: AuthenticatedActor,
        source: CallbackSource,
        credential: dict[str, Any],
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, source.connection_id, lock=True)
            oauth_session = await session.get(MCPOAuthSessionRecord, source.session.id, with_for_update=True)
            if oauth_session is None or not _callback_claim_matches(oauth_session, source):
                raise MCPConnectionError(
                    "oauth_callback_lost_race", "OAuth callback changed concurrently.", category=ErrorCategory.conflict
                )
            await authorize_connection(session, actor, connection, mode="manage")
            if (
                connection.status != "pending"
                or connection.version != source.connection_version
                or connection.credential_generation != source.credential_generation
                or oauth_session.claim_expires_at is None
                or assume_utc(oauth_session.claim_expires_at) <= now
                or assume_utc(oauth_session.expires_at) <= now
            ):
                raise MCPConnectionError(
                    "oauth_callback_lost_race",
                    "Authorization changed during exchange.",
                    category=ErrorCategory.conflict,
                )
            value = canonical_json(validate_oauth_bundle(credential))
            connection.replace_credential(value, self._protector)
            oauth_session.clear_credential()

            connection.status = "pending"
            connection.status_reason = None
            connection.updated_at = now
            invalidate_refresh_claim(connection, now=now)
            oauth_session.status = "completed"
            oauth_session.consumed_at = now
            oauth_session.claim_owner = None
            oauth_session.claim_expires_at = None
            oauth_session.updated_at = now
            session.add(audit(actor, connection, action="mcp_connection.oauth.complete", now=now))

    async def _callback_failed(self, source: CallbackSource, error: Exception) -> None:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, source.connection_id, lock=True, include_deleted=True)
            oauth_session = await session.get(MCPOAuthSessionRecord, source.session.id, with_for_update=True)
            if oauth_session is None or not _callback_claim_matches(oauth_session, source):
                return
            oauth_session.last_error_code = (
                error.code if isinstance(error, MCPOAuthError) else "oauth_callback_failed"
            )[:128]
            oauth_session.claim_owner = None
            oauth_session.claim_expires_at = None
            oauth_session.updated_at = self._clock()
            oauth_session.status = "failed"
            oauth_session.clear_credential()
            if (
                connection.deleted_at is None
                and connection.status == "pending"
                and connection.version == source.connection_version
                and connection.credential_generation == source.credential_generation
            ):
                connection.status = "action_required"
                connection.status_reason = "reauthorization_required"
                connection.updated_at = oauth_session.updated_at

    async def _mark_incompatible(self, connection_id: str, version: int) -> None:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            if connection.version != version or connection.status == "disabled":
                return
            connection.status = "action_required"
            connection.status_reason = "incompatible"
            connection.updated_at = self._clock()


@dataclass(frozen=True, slots=True)
class CallbackSource:
    connection_id: str
    connection_version: int
    credential_generation: int
    session: OAuthSessionSnapshot
    claim_generation: int
    claim_owner: str


def _source_matches(connection: MCPConnectionRecord, source: OAuthSource | MachineOAuthSource) -> bool:
    return (
        connection.deleted_at is None
        and connection.auth_mode == "oauth"
        and connection.status != "disabled"
        and connection.endpoint_url == source.endpoint_url
        and connection.version == source.version
    )


def _callback_claim_matches(session: MCPOAuthSessionRecord, source: CallbackSource) -> bool:
    return (
        session.status == "exchanging"
        and session.claim_generation == source.claim_generation
        and session.claim_owner == source.claim_owner
    )


def _require_user(actor: AuthenticatedActor) -> None:
    if actor.principal.principal_type is not PrincipalType.user:
        raise MCPConnectionError(
            "interactive_user_required", "OAuth requires an interactive User.", category=ErrorCategory.forbidden
        )


def _idempotency_digest(value: str) -> str:
    try:
        return digest_visible_ascii_key(value)
    except InvalidIdempotencyKey as error:
        raise map_management_error(error) from error


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
