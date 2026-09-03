"""Short-transaction ConnectorConnection setup orchestration."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.adapters import (
    ConnectionInspection,
    ConnectorAdapter,
    ConnectorAdapterError,
    SetupContext,
    SetupStarted,
)
from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.connectivity.management import canonical_digest
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.secrets import InternalSecretError, InternalSecretService, SecretOperation
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .connection_access import (
    apply_inspection,
    connection_resource,
    external_error,
    verify_inspection,
)
from .domain import ConnectorSetupLaunch
from .errors import ConnectorError
from .management import (
    audit,
    decode_credentials,
    require_adapter,
    require_connection,
    require_connector,
    secret_context,
)
from .models import ConnectorConnectionRecord, ConnectorRecord, ConnectorSetupAttemptRecord


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    attempt: ConnectorSetupAttemptRecord
    connector: ConnectorRecord
    credentials: JsonObject


class ConnectorSetupCoordinator:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[ConnectorAdapter],
        secrets: InternalSecretService,
        *,
        correlation_secret: bytes | None,
        public_origin: str | None,
        setup_ttl_seconds: int,
        clock: Clock = utc_now,
    ) -> None:
        if correlation_secret is not None and len(correlation_secret) < 32:
            raise ValueError("Connector setup correlation secret must be at least 32 bytes")
        self._sessions = sessions
        self._adapters = adapters
        self._secrets = secrets
        self._correlation_secret = correlation_secret
        self._public_origin = public_origin.rstrip("/") if public_origin is not None else None
        self._setup_ttl_seconds = setup_ttl_seconds
        self._clock = clock

    def new_attempt(
        self,
        connection: ConnectorConnectionRecord,
        *,
        connector: ConnectorRecord,
        actor: AuthenticatedActor,
        attempt_id: str,
        setup: JsonObject,
        return_path: str,
        now: datetime,
    ) -> ConnectorSetupAttemptRecord:
        correlation = self.correlation(connector, _owner_ref(connection))
        return ConnectorSetupAttemptRecord(
            id=attempt_id,
            organization_id=connection.organization_id,
            workspace_id=connection.workspace_id,
            connector_connection_id=connection.id,
            generation=connection.setup_generation,
            initiating_principal_type=actor.principal.principal_type.value,
            initiating_principal_id=actor.principal.principal_id,
            owner_type=connection.owner_type,
            owner_id=connection.owner_id,
            driver_key=connector.driver_key,
            provider_key=connection.provider_key,
            external_user_correlation=correlation,
            state_digest=canonical_digest({"attempt_id": attempt_id, "correlation": correlation}),
            return_path=return_path,
            setup_json=setup,
            external_ref=None,
            external_handle_digest=None,
            supports_verified_callback=False,
            status="pending",
            available_at=now,
            expires_at=now + timedelta(seconds=self._setup_ttl_seconds),
            reserved_at=None,
            consumed_at=None,
            attempt_count=0,
            claim_generation=0,
            claim_owner=None,
            claim_expires_at=None,
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )

    async def launch(self, attempt_id: str, connection_id: str) -> ConnectorSetupLaunch:
        async with short_session(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
            if attempt is None:
                raise ConnectorError("setup_unavailable", "Connector setup is unavailable.", status_code=404)
            if attempt.status in {"completed", "failed", "expired"}:
                return await self._receipt(session, attempt, connection_id=connection_id, redirect_url=None)
        started = await self.start_attempt(attempt_id)
        async with short_session(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
            if attempt is None:
                raise ConnectorError("setup_unavailable", "Connector setup is unavailable.", status_code=404)
            return await self._receipt(
                session,
                attempt,
                connection_id=connection_id,
                redirect_url=started.redirect_url,
            )

    async def _receipt(
        self,
        session: AsyncSession,
        attempt: ConnectorSetupAttemptRecord,
        *,
        connection_id: str,
        redirect_url: str | None,
    ) -> ConnectorSetupLaunch:
        status = attempt.status if attempt.status in {"completed", "failed", "expired"} else "pending"
        return ConnectorSetupLaunch.model_validate(
            {
                "attempt_id": attempt.id,
                "status": status,
                "expires_at": assume_utc(attempt.expires_at),
                "connection": await connection_resource(session, connection_id),
                "redirect_url": redirect_url,
            }
        )

    async def complete_callback(self, *, actor: AuthenticatedActor, session_uri: str) -> str:
        if actor.principal.principal_type is not PrincipalType.user:
            raise ConnectorError("interactive_user_required", "Interactive setup requires a User.", status_code=403)
        if not 1 <= len(session_uri) <= 4096:
            raise ConnectorError("invalid_callback", "Connector setup callback is invalid.", status_code=400)
        digest = canonical_digest(session_uri)
        now = self._clock()
        async with transaction(self._sessions) as session:
            attempt = await session.scalar(
                select(ConnectorSetupAttemptRecord)
                .where(
                    ConnectorSetupAttemptRecord.external_handle_digest == digest,
                    ConnectorSetupAttemptRecord.status == "attached",
                )
                .with_for_update()
            )
            if (
                attempt is None
                or attempt.initiating_principal_id != actor.principal.principal_id
                or assume_utc(attempt.expires_at) <= now
                or not attempt.supports_verified_callback
                or attempt.external_ref is None
            ):
                raise ConnectorError("invalid_callback", "Connector setup callback is invalid.", status_code=400)
            attempt.status = "reserved"
            attempt.reserved_at = now
            attempt.updated_at = now
            attempt_id = attempt.id
        try:
            inspection = await self._complete_attempt(attempt_id, session_uri=session_uri)
        except ConnectorAdapterError as error:
            async with transaction(self._sessions) as session:
                attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
                if attempt is not None and attempt.status == "reserved":
                    attempt.status = "attached"
                    attempt.reserved_at = None
                    attempt.last_error_code = error.code
                    attempt.updated_at = self._clock()
            raise external_error(error) from error
        try:
            await self.finish_attempt(attempt_id, inspection, actor=actor)
        except ConnectorError as error:
            async with transaction(self._sessions) as session:
                attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
                if attempt is not None and attempt.status == "reserved":
                    attempt.status = "failed"
                    attempt.reserved_at = None
                    attempt.last_error_code = error.code
                    attempt.updated_at = self._clock()
            raise
        async with short_session(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
            if attempt is None:
                raise ConnectorError("invalid_callback", "Connector setup callback is invalid.", status_code=400)
            return attempt.return_path

    async def start_attempt(
        self,
        attempt_id: str,
        *,
        claim_owner: str | None = None,
        claim_generation: int | None = None,
    ) -> SetupStarted:
        snapshot = await self.attempt_snapshot(attempt_id, operation=SecretOperation.setup)
        adapter = require_adapter(self._adapters, snapshot.connector.driver_key, snapshot.connector.config_version)
        try:
            started = await adapter.start_setup(
                endpoint=snapshot.connector.endpoint,
                connector_config=snapshot.connector.config_json,
                credentials=snapshot.credentials,
                setup=snapshot.attempt.setup_json,
                context=_setup_context(snapshot.attempt, callback_url=self.callback_url()),
            )
        except ConnectorAdapterError as error:
            await self.record_attempt_failure(
                attempt_id,
                error,
                claim_owner=claim_owner,
                claim_generation=claim_generation,
            )
            raise external_error(error) from error
        async with transaction(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
            if (
                attempt is None
                or attempt.status not in {"pending", "attached"}
                or not _claim_matches(attempt, claim_owner, claim_generation)
            ):
                raise ConnectorError("setup_lost_race", "Connector setup changed concurrently.", status_code=409)
            connection = await require_connection(session, attempt.connector_connection_id, lock=True)
            if connection.setup_generation != attempt.generation:
                raise ConnectorError("setup_lost_race", "Connector setup changed concurrently.", status_code=409)
            if connection.external_ref is not None and connection.external_ref != started.external_ref:
                attempt.status = "failed"
                attempt.last_error_code = "connection_substitution"
                raise ConnectorError(
                    "connection_substitution",
                    "Connector returned another external account.",
                    status_code=409,
                )
            now = self._clock()
            connection.external_ref = started.external_ref
            connection.updated_at = now
            attempt.external_ref = started.external_ref
            attempt.external_handle_digest = (
                canonical_digest(started.external_handle) if started.external_handle is not None else None
            )
            attempt.supports_verified_callback = started.supports_verified_callback
            attempt.status = "attached"
            attempt.available_at = now
            attempt.updated_at = now
        return started

    async def finish_attempt(
        self,
        attempt_id: str,
        inspection: ConnectionInspection,
        *,
        actor: AuthenticatedActor | None = None,
        claim_owner: str | None = None,
        claim_generation: int | None = None,
    ) -> None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
            if (
                attempt is None
                or attempt.status not in {"attached", "reserved"}
                or not _claim_matches(attempt, claim_owner, claim_generation)
            ):
                return
            connection = await require_connection(session, attempt.connector_connection_id, lock=True)
            verify_inspection(attempt, connection, inspection)
            apply_inspection(connection, inspection, now=now)
            attempt.status = "completed"
            attempt.consumed_at = now
            attempt.reserved_at = None
            attempt.last_error_code = None
            attempt.updated_at = now
            if actor is not None:
                session.add(
                    audit(
                        actor,
                        organization_id=connection.organization_id,
                        workspace_id=connection.workspace_id,
                        action="connector_connection.setup.complete",
                        resource_type="connector_connection",
                        resource_id=connection.id,
                        now=now,
                    )
                )

    async def attempt_snapshot(self, attempt_id: str, *, operation: SecretOperation) -> AttemptSnapshot:
        async with short_session(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id)
            if attempt is None:
                raise ConnectorError("setup_unavailable", "Connector setup is unavailable.", status_code=404)
            connection = await require_connection(session, attempt.connector_connection_id)
            connector = await require_connector(session, connection.connector_id)
        try:
            raw = await self._secrets.resolve(secret_context(connector, operation=operation))
        except InternalSecretError as error:
            raise ConnectorError(
                "credential_unavailable", "Connector credentials are unavailable.", status_code=503
            ) from error
        return AttemptSnapshot(attempt=attempt, connector=connector, credentials=decode_credentials(raw))

    async def record_attempt_failure(
        self,
        attempt_id: str,
        error: ConnectorAdapterError,
        *,
        claim_owner: str | None,
        claim_generation: int | None,
    ) -> None:
        async with transaction(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
            if (
                attempt is None
                or attempt.status != "pending"
                or not _claim_matches(attempt, claim_owner, claim_generation)
            ):
                return
            now = self._clock()
            attempt.attempt_count += 1
            attempt.last_error_code = error.code
            attempt.available_at = now + timedelta(seconds=error.retry_after_seconds or 5)
            attempt.updated_at = now
            if not error.retryable and not error.outcome_unknown:
                attempt.status = "failed"

    async def fail_attempt(self, attempt_id: str, *, code: str) -> None:
        async with transaction(self._sessions) as session:
            attempt = await session.get(ConnectorSetupAttemptRecord, attempt_id, with_for_update=True)
            if attempt is None or attempt.status not in {"pending", "attached", "reserved"}:
                return
            attempt.status = "failed"
            attempt.reserved_at = None
            attempt.last_error_code = code
            attempt.updated_at = self._clock()

    def correlation(self, connector: ConnectorRecord, owner: PrincipalRef | None) -> str:
        if self._correlation_secret is None:
            raise ConnectorError(
                "setup_unavailable",
                "Connector setup correlation is not configured.",
                status_code=503,
            )
        owner_kind = owner.principal_type.value if owner is not None else "workspace"
        owner_id = owner.principal_id if owner is not None else connector.workspace_id
        payload = "\0".join(
            (
                "a13n.connector-user.v1",
                connector.organization_id,
                connector.workspace_id,
                owner_kind,
                owner_id,
                connector.id,
            )
        ).encode()
        digest = hmac.new(self._correlation_secret, payload, hashlib.sha256).digest()
        return "usrh_" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def callback_url(self) -> str | None:
        if self._public_origin is None:
            return None
        return f"{self._public_origin}/connectivity/v1/connector-setup/callback"

    async def _complete_attempt(self, attempt_id: str, *, session_uri: str) -> ConnectionInspection:
        snapshot = await self.attempt_snapshot(attempt_id, operation=SecretOperation.callback)
        if snapshot.attempt.external_ref is None:
            raise ConnectorAdapterError("setup_incomplete")
        adapter = require_adapter(self._adapters, snapshot.connector.driver_key, snapshot.connector.config_version)
        return await adapter.complete_setup(
            endpoint=snapshot.connector.endpoint,
            connector_config=snapshot.connector.config_json,
            credentials=snapshot.credentials,
            session_uri=session_uri,
            context=_setup_context(snapshot.attempt, callback_url=self.callback_url()),
            expected_external_ref=snapshot.attempt.external_ref,
        )


def _setup_context(attempt: ConnectorSetupAttemptRecord, *, callback_url: str | None) -> SetupContext:
    return SetupContext(
        attempt_id=attempt.id,
        generation=attempt.generation,
        provider_key=attempt.provider_key,
        external_user_correlation=attempt.external_user_correlation,
        callback_url=callback_url,
    )


def _owner_ref(connection: ConnectorConnectionRecord) -> PrincipalRef | None:
    if connection.owner_type is None or connection.owner_id is None:
        return None
    return PrincipalRef(principal_type=PrincipalType(connection.owner_type), principal_id=connection.owner_id)


def _claim_matches(
    attempt: ConnectorSetupAttemptRecord,
    claim_owner: str | None,
    claim_generation: int | None,
) -> bool:
    if claim_owner is None or claim_generation is None:
        return claim_owner is None and claim_generation is None and attempt.claim_owner is None
    return attempt.claim_owner == claim_owner and attempt.claim_generation == claim_generation
