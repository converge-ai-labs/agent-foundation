"""Durable MCP OAuth authorization, callback, and refresh orchestration."""

from __future__ import annotations

import base64
import hashlib
import secrets as random_secrets
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

import httpx2
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import (
    ConnectivityManagementValueError,
    canonical_digest,
    canonical_json,
    idempotency_key_digest,
    record_command,
    replay_command,
)
from a13n_service.credentials import CredentialSnapshot
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
    optional_expiration,
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
class RefreshSource:
    connection_id: str
    organization_id: str
    workspace_id: str
    credential_generation: int
    claim_generation: int
    claim_owner: str
    credential: CredentialSnapshot


class MCPOAuthService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        oauth: MCPOAuthClient,
        protector: SecretProtector,
        discovery: ConnectionDiscovery,
        *,
        public_origin: str,
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
        self._public_origin = public_origin.rstrip("/")
        self._client_name = client_name
        self._instance_id = instance_id
        self._setup_ttl_seconds = setup_ttl_seconds
        self._claim_lease_seconds = claim_lease_seconds
        self._clock = clock

    @property
    def client_metadata_url(self) -> str:
        return f"{self._public_origin}/api/v1/oauth/mcp/client-metadata.json"

    @property
    def redirect_uri(self) -> str:
        return f"{self._public_origin}/api/v1/oauth/mcp/callback"

    async def authorize(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
    ) -> MCPAuthorizationLaunch:
        _require_user(actor)
        key_digest = _idempotency_digest(idempotency_key)
        request_fingerprint = canonical_digest({"expected_version": expected_version})
        source = await self._authorize_source(
            actor=actor,
            connection_id=connection_id,
            expected_version=expected_version,
            key_digest=key_digest,
            request_fingerprint=request_fingerprint,
        )
        if isinstance(source, MCPOAuthSessionRecord):
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
                status_code=409 if incompatible else 503,
            ) from error
        except httpx2.HTTPError as error:
            raise MCPConnectionError(
                "mcp_oauth_unavailable",
                "Remote MCP OAuth setup is temporarily unavailable.",
                status_code=503,
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
            preparation = oauth_preparation(source.session, setup)
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
                status_code=503 if unavailable else 409,
            ) from error
        except httpx2.HTTPError as error:
            await self._callback_failed(source, error)
            raise MCPConnectionError(
                "mcp_oauth_unavailable",
                "Remote MCP authorization is temporarily unavailable.",
                status_code=503,
            ) from error
        await self._discovery.discover(source.connection.id)
        async with transaction(self._sessions) as session:
            record = await require_connection(session, source.connection.id)
            await authorize_connection(session, actor, record, mode="read")
            return record.to_resource()

    async def refresh_credentials(self, connection_id: str) -> bool:
        source = await self._claim_refresh(connection_id)
        if source is None:
            return False
        try:
            raw = source.credential.decrypt(self._protector)
            bundle = decode_oauth_bundle(raw)
            candidate = with_expiration(await self._oauth.refresh(dict(bundle)), self._clock())
        except MCPOAuthError as error:
            await self._finish_refresh(source, action_required=error.action_required, error_code=error.code)
            return False
        except (SecretProtectionError, ValueError):
            await self._finish_refresh(source, action_required=False, error_code="credential_unavailable")
            return False
        except httpx2.HTTPError:
            await self._finish_refresh(source, action_required=False, error_code="oauth_unavailable")
            return False
        async with transaction(self._sessions) as session:
            current = await require_connection(session, connection_id, lock=True)
            if not _refresh_claim_matches(current, source):
                return False
            current.replace_credential(canonical_json(candidate), self._protector)

            current.refresh_claim_owner = None
            current.refresh_claim_expires_at = None
            current.refresh_available_at = self._clock()
            current.refresh_last_error_code = None
            current.updated_at = current.refresh_available_at
        return True

    async def refresh_if_due(self, connection_id: str, *, skew_seconds: int = 60) -> bool | None:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            if connection.auth_mode != "oauth" or connection.ciphertext is None:
                return None
            generation = connection.credential_generation
        try:
            raw = connection.credential_snapshot().decrypt(self._protector)
            expires_at = optional_expiration(decode_oauth_bundle(raw).get("expires_at"))
        except (SecretProtectionError, ValueError):
            async with transaction(self._sessions) as session:
                current = await require_connection(session, connection_id, lock=True)
                if current.credential_generation == generation:
                    current.refresh_available_at = self._clock() + timedelta(seconds=60)
            return False
        if expires_at is None or expires_at > self._clock() + timedelta(seconds=skew_seconds):
            async with transaction(self._sessions) as session:
                current = await require_connection(session, connection_id, lock=True)
                if current.credential_generation == generation:
                    current.refresh_available_at = (
                        (expires_at - timedelta(seconds=skew_seconds))
                        if expires_at
                        else self._clock() + timedelta(hours=1)
                    )
            return None
        return await self.refresh_credentials(connection_id)

    async def cleanup_expired_session(self, session_id: str) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            oauth_session = await session.get(MCPOAuthSessionRecord, session_id, with_for_update=True)
            if oauth_session is None or oauth_session.status != "expired" or oauth_session.consumed_at is not None:
                return True
            if oauth_session.claim_expires_at is not None and assume_utc(oauth_session.claim_expires_at) > now:
                return False
            await require_connection(
                session,
                oauth_session.mcp_connection_id,
                include_deleted=True,
            )
            oauth_session.claim_generation += 1
            oauth_session.claim_owner = self._instance_id
            oauth_session.claim_expires_at = now + timedelta(seconds=self._claim_lease_seconds)
            claim_generation = oauth_session.claim_generation
        try:
            setup = self._resolve_setup(oauth_session)
            cleaned = await self._oauth.cleanup_registration_bundle(dict(setup))
            unavailable = False
        except (SecretProtectionError, ValueError):
            cleaned = False
            unavailable = True
        async with transaction(self._sessions) as session:
            current = await session.get(MCPOAuthSessionRecord, session_id, with_for_update=True)
            if (
                current is None
                or current.status != "expired"
                or current.claim_generation != claim_generation
                or current.claim_owner != self._instance_id
            ):
                return False
            await require_connection(
                session,
                current.mcp_connection_id,
                include_deleted=True,
            )
            if cleaned:
                current.clear_credential()
                current.consumed_at = self._clock()
                current.last_error_code = None
            elif unavailable:
                current.clear_credential()
                current.consumed_at = self._clock()
                current.last_error_code = "oauth_setup_credential_unavailable"
            else:
                current.last_error_code = "registration_cleanup_failed"
            current.claim_owner = None
            current.claim_expires_at = (
                None if cleaned or unavailable else self._clock() + timedelta(seconds=self._claim_lease_seconds)
            )
            current.updated_at = self._clock()
            return cleaned

    async def _authorize_source(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        key_digest: str,
        request_fingerprint: str,
    ) -> OAuthSource | MCPOAuthSessionRecord:
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id)
            await authorize_connection(session, actor, connection, mode="owner_manage")
            if connection.auth_mode != "oauth":
                raise MCPConnectionError(
                    "invalid_auth_mode",
                    "MCPConnection does not use OAuth.",
                    status_code=409,
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
                )
            except ConnectivityManagementValueError as error:
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
                        status_code=409,
                    )
                return oauth_session
            require_version(connection.version, expected_version)
            if connection.status == "disabled":
                raise MCPConnectionError("connection_disabled", "MCPConnection is disabled.", status_code=409)
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
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
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
                    status_code=409,
                )
            await session.execute(
                update(MCPOAuthSessionRecord)
                .where(
                    MCPOAuthSessionRecord.mcp_connection_id == connection.id,
                    MCPOAuthSessionRecord.status.in_(("pending", "exchanging")),
                )
                .values(
                    status="expired",
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
                state_digest=_digest(state),
                resource_url=preparation.resource_url,
                issuer_url=preparation.issuer_url,
                authorization_endpoint=preparation.authorization_endpoint,
                token_endpoint=preparation.token_endpoint,
                registration_endpoint=preparation.registration_endpoint,
                client_id=None,
                scope=None,
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
            )
            session.add(audit(actor, connection, action="mcp_connection.authorize", now=now))
            await session.flush()
        return MCPAuthorizationLaunch(
            id=session_id,
            authorization_url=authorization_url(
                preparation,
                redirect_uri=self.redirect_uri,
                state=state,
                code_challenge=challenge,
            ),
            expires_at=expires_at,
        )

    async def _launch_from_session(self, oauth_session: MCPOAuthSessionRecord) -> MCPAuthorizationLaunch:
        async with transaction(self._sessions) as session:
            await require_connection(session, oauth_session.mcp_connection_id)
        setup = self._resolve_setup(oauth_session)
        preparation = oauth_preparation(oauth_session, setup)
        verifier = required_oauth_string(setup, "verifier")
        return MCPAuthorizationLaunch(
            id=oauth_session.id,
            authorization_url=authorization_url(
                preparation,
                redirect_uri=self.redirect_uri,
                state=required_oauth_string(setup, "state"),
                code_challenge=_b64url(hashlib.sha256(verifier.encode()).digest()),
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
            oauth_session = await session.scalar(
                select(MCPOAuthSessionRecord)
                .where(MCPOAuthSessionRecord.state_digest == _digest(state))
                .with_for_update()
            )
            if oauth_session is None or oauth_session.initiating_user_id != actor.principal.principal_id:
                raise MCPConnectionError("invalid_oauth_state", "OAuth callback state is invalid.", status_code=400)
            connection = await require_connection(session, oauth_session.mcp_connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="owner_manage")
            if assume_utc(oauth_session.expires_at) <= now:
                oauth_session.status = "expired"
                oauth_session.updated_at = now
                raise MCPConnectionError(
                    "oauth_session_expired", "OAuth authorization session expired.", status_code=409
                )
            if issuer != oauth_session.issuer_url:
                raise MCPConnectionError("oauth_issuer_mismatch", "OAuth callback issuer is invalid.", status_code=400)
            if oauth_session.status == "completed":
                raise MCPConnectionError(
                    "oauth_state_replayed", "OAuth callback state was already used.", status_code=409
                )
            if oauth_session.status not in {"pending", "exchanging"} or (
                oauth_session.status == "exchanging"
                and oauth_session.claim_expires_at is not None
                and assume_utc(oauth_session.claim_expires_at) > now
            ):
                raise MCPConnectionError("oauth_session_unavailable", "OAuth session is unavailable.", status_code=409)
            oauth_session.status = "exchanging"
            oauth_session.claim_generation += 1
            oauth_session.claim_owner = self._instance_id
            oauth_session.claim_expires_at = now + timedelta(seconds=self._claim_lease_seconds)
            oauth_session.updated_at = now
            return CallbackSource(
                connection=connection,
                session=oauth_session,
                claim_generation=oauth_session.claim_generation,
                claim_owner=self._instance_id,
            )

    def _resolve_setup(
        self,
        oauth_session: MCPOAuthSessionRecord,
    ) -> JsonObject:
        raw = oauth_session.credential_snapshot().decrypt(self._protector)
        return decode_oauth_bundle(raw)

    async def _complete_callback(
        self,
        actor: AuthenticatedActor,
        source: CallbackSource,
        credential: dict[str, Any],
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            oauth_session = await session.get(MCPOAuthSessionRecord, source.session.id, with_for_update=True)
            connection = await require_connection(session, source.connection.id, lock=True)
            if oauth_session is None or not _callback_claim_matches(oauth_session, source):
                raise MCPConnectionError(
                    "oauth_callback_lost_race", "OAuth callback changed concurrently.", status_code=409
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
            oauth_session = await session.get(MCPOAuthSessionRecord, source.session.id, with_for_update=True)
            if oauth_session is None or not _callback_claim_matches(oauth_session, source):
                return
            oauth_session.last_error_code = (
                error.code if isinstance(error, MCPOAuthError) else "oauth_callback_failed"
            )[:128]
            oauth_session.claim_owner = None
            oauth_session.claim_expires_at = None
            oauth_session.updated_at = self._clock()
            if isinstance(error, MCPOAuthError) and error.action_required:
                oauth_session.status = "failed"
                connection = await require_connection(session, source.connection.id, lock=True)
                oauth_session.clear_credential()
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

    async def _claim_refresh(self, connection_id: str) -> RefreshSource | None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            if (
                connection.auth_mode != "oauth"
                or connection.ciphertext is None
                or connection.status == "disabled"
                or (
                    connection.refresh_claim_expires_at is not None
                    and assume_utc(connection.refresh_claim_expires_at) > now
                )
            ):
                return None
            connection.refresh_claim_generation += 1
            connection.refresh_claim_owner = self._instance_id
            connection.refresh_claim_expires_at = now + timedelta(seconds=self._claim_lease_seconds)
            return RefreshSource(
                connection_id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                credential_generation=connection.credential_generation,
                credential=connection.credential_snapshot(),
                claim_generation=connection.refresh_claim_generation,
                claim_owner=self._instance_id,
            )

    async def _finish_refresh(
        self,
        source: RefreshSource,
        *,
        action_required: bool,
        error_code: str,
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, source.connection_id, lock=True)
            if not _refresh_claim_matches(connection, source):
                return
            connection.refresh_claim_owner = None
            connection.refresh_claim_expires_at = None
            connection.refresh_available_at = now + timedelta(seconds=60)
            connection.refresh_last_error_code = error_code[:128]
            if action_required:
                connection.status = "action_required"
                connection.status_reason = "reauthorization_required"
                connection.updated_at = now


@dataclass(frozen=True, slots=True)
class CallbackSource:
    connection: MCPConnectionRecord
    session: MCPOAuthSessionRecord
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


def _refresh_claim_matches(connection: MCPConnectionRecord, source: RefreshSource) -> bool:
    return (
        connection.deleted_at is None
        and connection.status != "disabled"
        and connection.auth_mode == "oauth"
        and connection.credential_generation == source.credential_generation
        and connection.refresh_claim_generation == source.claim_generation
        and connection.refresh_claim_owner == source.claim_owner
    )


def _require_user(actor: AuthenticatedActor) -> None:
    if actor.principal.principal_type is not PrincipalType.user:
        raise MCPConnectionError("interactive_user_required", "OAuth requires an interactive User.", status_code=403)


def _idempotency_digest(value: str) -> str:
    try:
        return idempotency_key_digest(value)
    except ConnectivityManagementValueError as error:
        raise map_management_error(error) from error


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
