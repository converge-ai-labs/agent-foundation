"""Durable MCP OAuth authorization, callback, and refresh orchestration."""

from __future__ import annotations

import base64
import hashlib
import secrets as random_secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.credentials import CredentialSnapshot
from a13n_service.durable_operations.entity_keys import entity_key, find_by_key
from a13n_service.durable_operations.idempotency import (
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import new_object_id
from a13n_service.secrets import (
    SecretProtectionError,
    SecretProtector,
)
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from ..connections.handoff import store_material
from .domain import MCPAuthorizationLaunch, MCPOAuthClientInput
from .errors import MCPConnectionError
from .management import (
    audit,
    authorize_connection,
    invalidate_refresh_claim,
    map_management_error,
    require_connection,
    require_version,
)
from .models import MCPAuthorizationRecord, MCPConnectionOAuthClientRecord, MCPConnectionRecord
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
)
from .oauth_configuration import (
    OAuthConfiguration,
    client_configuration,
    client_refresh_context,
    configured_client,
    store_client,
)
from .service import ConnectionDiscovery


@dataclass(frozen=True, slots=True)
class OAuthSource:
    authorization_id: str
    connection_id: str
    organization_id: str
    workspace_id: str
    endpoint_url: str
    version: int
    client: MCPOAuthClientInput | None
    redirect_uri: str


@dataclass(frozen=True, slots=True)
class OAuthSessionSnapshot:
    id: str
    connection_id: str
    expires_at: datetime
    credential: CredentialSnapshot

    @classmethod
    def from_record(cls, record: MCPAuthorizationRecord) -> OAuthSessionSnapshot:
        return cls(record.id, record.connection_id, assume_utc(record.expires_at), record.credential_snapshot())


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
        redirect_uris: tuple[str, ...],
        documentation_urls: dict[str, str] | None = None,
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
        self._redirect_uris = redirect_uris
        self._documentation_urls = documentation_urls or {}
        self._client_name = client_name
        self._instance_id = instance_id
        self._setup_ttl_seconds = setup_ttl_seconds
        self._claim_lease_seconds = claim_lease_seconds
        self._clock = clock

    @property
    def configuration(self) -> OAuthConfiguration:
        return OAuthConfiguration(
            self._sessions,
            self._oauth,
            self._protector,
            redirect_uris=self._redirect_uris,
            documentation_urls=self._documentation_urls,
            clock=self._clock,
        )

    async def authorize(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
        redirect_uri: str,
    ) -> MCPAuthorizationLaunch:
        if redirect_uri not in self._redirect_uris:
            raise MCPConnectionError(
                "invalid_redirect_uri",
                "OAuth redirect URI is not registered for this deployment.",
                category=ErrorCategory.invalid_request,
            )
        key_digest = _idempotency_digest(idempotency_key)
        source = await self._authorize_source(
            actor=actor,
            connection_id=connection_id,
            expected_version=expected_version,
            key_digest=key_digest,
            redirect_uri=redirect_uri,
        )
        if isinstance(source, OAuthSessionSnapshot):
            return await self._launch_from_session(source)
        try:
            preparation = await self._oauth.prepare(
                source.endpoint_url,
                redirect_uri=source.redirect_uri,
                client_name=self._client_name,
                client=source.client,
            )
        except (MCPOAuthError, httpx2.HTTPError) as error:
            return await self._preparation_failed(source, error)
        try:
            return await self._create_session(
                actor=actor,
                source=source,
                preparation=preparation,
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
        expected_version: int,
    ) -> Connection:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="manage")
            require_version(connection.version, expected_version)
            if connection.status == "disabled":
                raise MCPConnectionError(
                    "connection_disabled", "Connection is disabled.", category=ErrorCategory.conflict
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
                    "This Connection does not use client credentials.",
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
            await authorize_connection(session, actor, connection, mode="manage")
            client_record = await session.get(MCPConnectionOAuthClientRecord, connection_id)
            if (
                not _source_matches(connection, source)
                or client_record is None
                or client_record.credential_generation != source.client_generation
            ):
                raise MCPConnectionError(
                    "version_conflict",
                    "Connection changed during machine authorization.",
                    category=ErrorCategory.conflict,
                )
            connection.replace_credential(canonical_json(validate_oauth_bundle(credential)), self._protector)
            connection.authorization_generation += 1
            connection.status = "pending"
            connection.status_reason = None
            connection.version += 1
            connection.updated_at = now
            invalidate_refresh_claim(connection, now=now)
            resource = connection.to_resource()
            session.add(audit(actor, connection, action="mcp_connection.client_credentials", now=now))
        try:
            return (
                await self._discovery.discover(connection_id, actor=actor, expected_version=resource.version)
            ).connection
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

    async def complete(
        self,
        *,
        actor: AuthenticatedActor,
        authorization_id: str,
        state: str,
        code: str | None,
        issuer: str | None,
        response_error: str | None,
    ) -> Connection:
        source = await self._reserve_callback(
            actor=actor,
            authorization_id=authorization_id,
            state=state,
            issuer=issuer,
        )
        if response_error is not None:
            failure = MCPOAuthError("authorization_rejected")
            await self._callback_failed(source, failure)
            raise MCPConnectionError(
                "mcp_oauth_rejected",
                "Remote MCP authorization was rejected.",
                category=ErrorCategory.conflict,
            )
        if code is None:
            raise MCPConnectionError(
                "invalid_oauth_response", "OAuth response is invalid.", category=ErrorCategory.invalid_request
            )
        try:
            setup = self._resolve_setup(source.session)
            preparation = oauth_preparation(setup)
            credential = await self._oauth.exchange_code(
                preparation,
                code=code,
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
        redirect_uri: str,
    ) -> OAuthSource | OAuthSessionSnapshot:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="manage")
            if connection.auth_mode != "oauth":
                raise MCPConnectionError(
                    "invalid_auth_mode",
                    "Connection does not use OAuth.",
                    category=ErrorCategory.conflict,
                )
            replay = await find_by_key(
                session,
                MCPAuthorizationRecord,
                entity_key(
                    actor,
                    operation="connection.authorize",
                    scope_id=connection.id,
                    key_digest=key_digest,
                    workspace_id=connection.workspace_id,
                ),
            )
            if replay is not None:
                oauth_session = replay
                if oauth_session is None or oauth_session.connection_id != connection.id:
                    raise MCPConnectionError(
                        "oauth_session_unavailable",
                        "OAuth authorization session is no longer available.",
                        category=ErrorCategory.conflict,
                    )
                return OAuthSessionSnapshot.from_record(oauth_session)
            require_version(connection.version, expected_version)
            if connection.status == "disabled":
                raise MCPConnectionError(
                    "connection_disabled", "Connection is disabled.", category=ErrorCategory.conflict
                )
            client_record = await session.get(MCPConnectionOAuthClientRecord, connection.id)
            client = configured_client(client_record, self._protector) if client_record is not None else None
            if client is not None and client.grant_type != "authorization_code":
                raise MCPConnectionError(
                    "invalid_oauth_grant",
                    "This Connection uses client credentials.",
                    category=ErrorCategory.conflict,
                )
            if client is not None and client.redirect_uri != redirect_uri:
                raise MCPConnectionError(
                    "oauth_client_redirect_mismatch",
                    "Configured OAuth client uses a different redirect URI.",
                    category=ErrorCategory.conflict,
                )
            now = self._clock()
            await session.execute(
                update(MCPAuthorizationRecord)
                .where(
                    MCPAuthorizationRecord.connection_id == connection.id,
                    MCPAuthorizationRecord.status.in_(("starting", "pending", "received", "exchanging")),
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
            connection.status = "pending"
            connection.status_reason = None
            connection.version += 1
            connection.setup_generation += 1
            connection.updated_at = now
            invalidate_refresh_claim(connection, now=now)
            authorization = MCPAuthorizationRecord(
                id=new_object_id("authz"),
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                connection_id=connection.id,
                initiating_principal_id=actor.principal.principal_id,
                initiating_principal_type=actor.principal.principal_type.value,
                completion_method="mcp_oauth",
                available_at=now,
                generation=connection.setup_generation,
                connection_version=connection.version,
                credential_generation=0,
                status="starting",
                claim_generation=0,
                expires_at=now + timedelta(seconds=self._setup_ttl_seconds),
                created_at=now,
                updated_at=now,
            )
            authorization.request_key = entity_key(
                actor,
                operation="connection.authorize",
                scope_id=connection.id,
                key_digest=key_digest,
                workspace_id=connection.workspace_id,
            )
            session.add(authorization)
            session.add(audit(actor, connection, action="connection.authorize", now=now))
            return OAuthSource(
                authorization_id=authorization.id,
                connection_id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                endpoint_url=connection.endpoint_url,
                version=connection.version,
                client=client,
                redirect_uri=redirect_uri,
            )

    async def _create_session(
        self,
        *,
        actor: AuthenticatedActor,
        source: OAuthSource,
        preparation: OAuthPreparation,
    ) -> MCPAuthorizationLaunch:
        state = random_secrets.token_urlsafe(32)
        verifier = random_secrets.token_urlsafe(64)
        now = self._clock()
        setup = oauth_setup_bundle(preparation, state=state, verifier=verifier)
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, source.connection_id, lock=True)
            if not _source_matches(connection, source):
                raise MCPConnectionError(
                    "version_conflict",
                    "Connection changed during OAuth discovery.",
                    category=ErrorCategory.conflict,
                )
            await authorize_connection(session, actor, connection, mode="manage")
            oauth_session = await session.get(MCPAuthorizationRecord, source.authorization_id, with_for_update=True)
            if (
                oauth_session is None
                or oauth_session.status != "starting"
                or assume_utc(oauth_session.expires_at) <= now
            ):
                raise MCPConnectionError(
                    "oauth_session_unavailable",
                    "OAuth preparation was superseded or expired.",
                    category=ErrorCategory.conflict,
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
                            "redirect_uri": preparation.redirect_uri,
                        }
                    ),
                    source="dynamic",
                    protector=self._protector,
                    resource_url=preparation.resource_url,
                    token_endpoint=preparation.token_endpoint,
                    scope=preparation.scope,
                    registration_access_token=preparation.registration_access_token,
                    registration_client_uri=preparation.registration_client_uri,
                )
                session.add(client_record)

            bundle = (
                self._resolve_setup(OAuthSessionSnapshot.from_record(oauth_session))
                if oauth_session.ciphertext is not None
                else {}
            )
            bundle.update(setup)
            oauth_session.replace_credential(canonical_json(bundle), self._protector)
            oauth_session.state_digest = _digest(state)
            oauth_session.status = "pending"
            oauth_session.updated_at = now
            store_material(
                oauth_session,
                self._protector,
                provider_url=authorization_url(preparation, state=state, code_verifier=verifier),
            )
            expires_at = assume_utc(oauth_session.expires_at)
            await session.flush()
        return MCPAuthorizationLaunch(
            id=source.authorization_id,
            authorization_url=authorization_url(
                preparation,
                state=state,
                code_verifier=verifier,
            ),
            expires_at=expires_at,
        )

    async def _launch_from_session(self, oauth_session: OAuthSessionSnapshot) -> MCPAuthorizationLaunch:
        async with transaction(self._sessions) as session:
            await require_connection(session, oauth_session.connection_id)
            current = await session.get(MCPAuthorizationRecord, oauth_session.id)
            if current is None or current.status != "pending":
                return MCPAuthorizationLaunch(
                    id=oauth_session.id,
                    status=current.status if current is not None else "expired",
                    authorization_url=None,
                    expires_at=oauth_session.expires_at,
                )
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
        authorization_id: str,
        state: str,
        issuer: str | None,
    ) -> CallbackSource:
        now = self._clock()
        async with transaction(self._sessions) as session:
            oauth_session = await session.get(MCPAuthorizationRecord, authorization_id)
            if oauth_session is None or oauth_session.state_digest != _digest(state):
                raise MCPConnectionError(
                    "invalid_oauth_state", "OAuth callback state is invalid.", category=ErrorCategory.invalid_request
                )
            connection = await require_connection(session, oauth_session.connection_id, lock=True)
            await session.refresh(oauth_session, with_for_update=True)
            if (
                oauth_session.state_digest != _digest(state)
                or oauth_session.initiating_principal_id != actor.principal.principal_id
                or oauth_session.initiating_principal_type != actor.principal.principal_type.value
            ):
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
                oauth_session.status != "pending"
                or connection.status != "pending"
                or connection.version != oauth_session.connection_version
            ):
                raise MCPConnectionError(
                    "oauth_session_unavailable",
                    "Start a new authorization session; the previous session is unavailable.",
                    category=ErrorCategory.conflict,
                )
            snapshot = OAuthSessionSnapshot.from_record(oauth_session)
            preparation = oauth_preparation(self._resolve_setup(snapshot))
            client_record = await session.get(MCPConnectionOAuthClientRecord, connection.id)
            configured_redirect = (
                client_configuration(client_record).redirect_uri if client_record is not None else None
            )
            if preparation.redirect_uri not in self._redirect_uris or configured_redirect != preparation.redirect_uri:
                raise MCPConnectionError(
                    "oauth_callback_mismatch",
                    "OAuth callback address is invalid.",
                    category=ErrorCategory.invalid_request,
                )
            if issuer is not None and issuer != preparation.issuer_url:
                raise MCPConnectionError(
                    "oauth_issuer_mismatch", "OAuth callback issuer is invalid.", category=ErrorCategory.invalid_request
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
            oauth_session = await session.get(MCPAuthorizationRecord, source.session.id, with_for_update=True)
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
            connection.authorization_generation += 1
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
            oauth_session = await session.get(MCPAuthorizationRecord, source.session.id, with_for_update=True)
            if oauth_session is None or not _callback_claim_matches(oauth_session, source):
                return
            oauth_session.last_error_code = (
                "setup_outcome_unknown"
                if isinstance(error, httpx2.HTTPError)
                or (isinstance(error, MCPOAuthError) and error.code == "token_exchange_unavailable")
                else error.code
                if isinstance(error, MCPOAuthError)
                else "oauth_callback_failed"
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

    async def _preparation_failed(self, source: OAuthSource, error: Exception) -> MCPAuthorizationLaunch:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, source.connection_id, lock=True, include_deleted=True)
            attempt = await session.get(MCPAuthorizationRecord, source.authorization_id, with_for_update=True)
            assert attempt is not None
            if attempt.status == "starting":
                attempt.status = "failed"
                attempt.last_error_code = (error.code if isinstance(error, MCPOAuthError) else "metadata_unavailable")[
                    :128
                ]
                attempt.clear_credential()
                if isinstance(error, MCPOAuthError) and error.details:
                    attempt.setup_json = {"oauth_setup": error.details}
                attempt.updated_at = self._clock()
                if _source_matches(connection, source):
                    connection.status = "action_required"
                    connection.status_reason = "incompatible"
                    connection.updated_at = attempt.updated_at
            return MCPAuthorizationLaunch(
                id=attempt.id, status=attempt.status, authorization_url=None, expires_at=assume_utc(attempt.expires_at)
            )


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


def _callback_claim_matches(session: MCPAuthorizationRecord, source: CallbackSource) -> bool:
    return (
        session.status == "exchanging"
        and session.claim_generation == source.claim_generation
        and session.claim_owner == source.claim_owner
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
