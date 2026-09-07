"""Durable MCP OAuth authorization, callback, and refresh orchestration."""

from __future__ import annotations

import base64
import hashlib
import secrets as random_secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json, record_command, replay_command
from a13n_service.credentials import CredentialSnapshot
from a13n_service.durable_operations.idempotency import (
    IdempotencyConflict,
    InvalidIdempotencyKey,
    digest_request,
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

from .domain import MCPAuthorizationLaunch, MCPConnection
from .errors import MCPConnectionError
from .management import (
    audit,
    authorize_connection,
    invalidate_refresh_claim,
    map_management_error,
    require_connection,
    require_version,
)
from .models import MCPConnectionRecord, MCPOAuthSessionRecord
from .oauth_bundles import (
    decode_oauth_bundle,
    oauth_preparation,
    oauth_setup_bundle,
    required_oauth_string,
    validate_oauth_bundle,
    with_expiration,
)
from .oauth_client import MCPOAuthClient, MCPOAuthError, OAuthPreparation, authorization_url
from .service import ConnectionDiscovery


@dataclass(frozen=True, slots=True)
class OAuthSource:
    connection_id: str
    organization_id: str
    workspace_id: str
    endpoint_url: str
    version: int


@dataclass(frozen=True, slots=True)
class OAuthSessionSnapshot:
    id: str
    mcp_connection_id: str
    expires_at: datetime
    credential: CredentialSnapshot

    @classmethod
    def from_record(cls, record: MCPOAuthSessionRecord) -> OAuthSessionSnapshot:
        return cls(record.id, record.mcp_connection_id, assume_utc(record.expires_at), record.credential_snapshot())


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

    @property
    def client_metadata_url(self) -> str:
        self._require_origin()
        return f"{self._public_origin}/api/v1/oauth/mcp/client-metadata.json"

    @property
    def redirect_uri(self) -> str:
        self._require_origin()
        return f"{self._public_origin}/api/v1/oauth/mcp/callback"

    def _require_origin(self) -> None:
        if self._public_origin is None:
            raise MCPConnectionError(
                "oauth_unavailable",
                "Interactive OAuth requires a configured public origin.",
                category=ErrorCategory.unavailable,
            )

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
                client_metadata_url=self.client_metadata_url,
                redirect_uri=self.redirect_uri,
                client_name=self._client_name,
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

    async def callback(
        self,
        *,
        actor: AuthenticatedActor,
        state: str,
        code: str,
        issuer: str,
    ) -> MCPConnection:
        _require_user(actor)
        source = await self._reserve_callback(actor=actor, state=state, issuer=issuer)
        try:
            setup = self._resolve_setup(source.session)
            preparation = oauth_preparation(setup)
            credential = await self._oauth.exchange_code(
                preparation,
                code=code,
                verifier=required_oauth_string(setup, "verifier"),
                redirect_uri=self.redirect_uri,
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
        await self._discovery.discover(source.connection_id)
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
            return OAuthSource(
                connection_id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                endpoint_url=connection.endpoint_url,
                version=connection.version,
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
                    MCPOAuthSessionRecord.status.in_(("pending", "exchanging")),
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
                redirect_uri=self.redirect_uri,
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
                redirect_uri=self.redirect_uri,
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
        issuer: str,
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
            if issuer != required_oauth_string(self._resolve_setup(snapshot), "issuer_url"):
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


def _source_matches(connection: MCPConnectionRecord, source: OAuthSource) -> bool:
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
