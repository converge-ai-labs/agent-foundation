"""Short-transaction Connection lifecycle orchestration."""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import record_command
from a13n_service.digests import digest_request
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.resource_scope import ResourceScope
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from ..connections.handoff import BrowserHandoff
from .connection_access import (
    authorize_connection,
    idempotency_digest,
    replay_connection_command,
    require_version,
)
from .domain import (
    ConnectorSetupLaunch,
)
from .errors import ConnectorError
from .management import (
    audit,
    require_connection,
    require_connector_provider,
    require_implementation,
)
from .models import (
    ConnectorAuthorizationRecord,
)
from .revocation import ConnectorRevocationService
from .setup import ConnectorSetupCoordinator, browser_digest


class ConnectorConnectionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: ConnectorProviderRegistry,
        protector: SecretProtector,
        *,
        correlation_secret: bytes | None,
        public_origin: str | None,
        setup_ttl_seconds: int,
        setup_lease_seconds: int = 60,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._protector = protector
        self._clock = clock
        self._setup = ConnectorSetupCoordinator(
            sessions,
            adapters,
            protector,
            correlation_secret=correlation_secret,
            public_origin=public_origin,
            setup_ttl_seconds=setup_ttl_seconds,
            setup_lease_seconds=setup_lease_seconds,
            clock=clock,
        )
        self._revocation = ConnectorRevocationService(sessions, adapters, protector, clock=clock)

    @property
    def setup_coordinator(self) -> ConnectorSetupCoordinator:
        return self._setup

    async def start_setup(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        idempotency_key: str,
        expected_version: int,
        setup: JsonObject,
        return_url: str,
        browser_nonce: str | None = None,
        handoff: BrowserHandoff | None = None,
        credentials: JsonObject | None = None,
    ) -> ConnectorSetupLaunch:
        key_digest = idempotency_digest(idempotency_key)
        request_fingerprint = digest_request(
            {
                "expected_version": expected_version,
                "setup": setup,
                "return_url": return_url,
                "browser_binding": browser_digest(browser_nonce),
                "handoff": asdict(handoff) if handoff is not None else None,
                "credentials_digest": digest_request(credentials) if credentials is not None else None,
            }
        )
        attempt_id = new_object_id("csa")
        initial_claim = None
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            await authorize_connection(session, actor, connection, mode="manage")
            replay = await replay_connection_command(
                session,
                actor=actor,
                connection=connection,
                operation="connector_connection.setup",
                key_digest=key_digest,
                request_fingerprint=request_fingerprint,
                now=self._clock(),
            )
            if replay is not None:
                attempt = await session.get(ConnectorAuthorizationRecord, replay.resource_id)
                if attempt is None:
                    raise ConnectorError(
                        "setup_unavailable", "ConnectorProvider setup is unavailable.", category=ErrorCategory.not_found
                    )
                attempt_id = attempt.id
            else:
                require_version(connection.version, expected_version)
                connector = await require_connector_provider(
                    session,
                    connection.connector_provider_id,
                    scope=ResourceScope(connection.organization_id, connection.workspace_id),
                )
                if connector.status != "active":
                    raise ConnectorError(
                        "connector_disabled", "ConnectorProvider is disabled.", category=ErrorCategory.conflict
                    )
                connection.setup_generation += 1
                connection.status = "pending"
                connection.status_reason = None
                connection.version += 1
                connection.updated_at = now
                adapter = require_implementation(self._adapters, connector.type)
                try:
                    validated_setup = adapter.validate_setup(
                        setup,
                        connector_key=connection.connector_key,
                        configuration=connector.configuration_json,
                    )
                except ValueError as error:
                    raise ConnectorError(
                        "invalid_connector_setup",
                        "ConnectorProvider setup is invalid.",
                        category=ErrorCategory.invalid_request,
                    ) from error
                attempt = self._setup.new_attempt(
                    connection,
                    connector=connector,
                    actor=actor,
                    attempt_id=attempt_id,
                    setup=validated_setup,
                    return_url=return_url,
                    now=now,
                    browser_nonce=browser_nonce,
                    handoff=handoff,
                    direct_credentials=credentials is not None,
                )
                session.add(attempt)
                assert attempt.claim_owner is not None
                initial_claim = (attempt.claim_owner, attempt.claim_generation)
                record_command(
                    session,
                    actor=actor,
                    organization_id=connection.organization_id,
                    workspace_id=connection.workspace_id,
                    operation="connector_connection.setup",
                    scope_id=connection.id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="connector_setup_attempt",
                    resource_id=attempt_id,
                    result_version=connection.version,
                    now=now,
                    resource=None,
                )
                session.add(
                    audit(
                        actor,
                        organization_id=connection.organization_id,
                        workspace_id=connection.workspace_id,
                        action="connector_connection.setup",
                        resource_type="connector_connection",
                        resource_id=connection.id,
                        now=now,
                    )
                )
        return await self._setup.launch(attempt_id, connection_id, initial_claim=initial_claim, credentials=credentials)

    async def revoke(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectionCleanupReceipt:
        return await self._revocation.invalidate(
            delete=False,
            actor=actor,
            connection_id=connection_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        connection_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> ConnectionCleanupReceipt:
        return await self._revocation.invalidate(
            delete=True,
            actor=actor,
            connection_id=connection_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    async def complete_callback(
        self,
        *,
        actor: AuthenticatedActor,
        session_uri: str | None = None,
        attempt_id: str,
        browser_nonce: str,
    ) -> str:
        return await self._setup.complete_callback(
            actor=actor,
            attempt_id=attempt_id,
            browser_nonce=browser_nonce,
            session_uri=session_uri,
        )
