"""Short database scopes for client connection eligibility and fenced observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from a13n_environment import EnvironmentState
from a13n_environment.remote_envd.configuration import RemoteEnvdProviderConfiguration
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_environment.remote_envd.environment import REQUIRED_METHODS, decode_state
from sqlalchemy import select

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, next_updated_at, utc_now

from ..access import authorize_environment_resource
from ..domain import EnvironmentConfiguration
from ..errors import environment_not_found, invalid_environment
from ..models import EnvironmentProviderRecord, EnvironmentRecord
from ..service import EnvironmentService


@dataclass(frozen=True, slots=True)
class ConnectionTarget:
    organization_id: str
    workspace_id: str
    environment_id: str
    provider_id: str
    provider_enabled: bool
    daemon_environment_id: str
    required_methods: frozenset[str]
    target_identity: str | None
    generation: int
    status: str
    updated_at: datetime
    provider_updated_at: datetime
    operation_generation: int
    operation_id: str | None

    def require_eligible(self) -> None:
        if not self.provider_enabled:
            raise invalid_environment("Client WebSocket Provider is disabled")


class ConnectionResources:
    def __init__(self, environments: EnvironmentService) -> None:
        self._environments = environments
        self._sessions = environments.sessions

    async def authorized(
        self, actor: AuthenticatedActor, environment_id: str, *, manage: bool = False
    ) -> ConnectionTarget:
        async with short_session(self._sessions) as session:
            row = await self._environments.require_environment(session, actor, environment_id)
            if manage:
                await authorize_environment_resource(
                    session,
                    actor=actor,
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    action=WorkspaceAction.environment_manage,
                    manage=True,
                )
            provider = await session.get(EnvironmentProviderRecord, row.provider_id)
            target = self._target(row, provider)
            if manage:
                target.require_eligible()
            return target

    async def capture(self, organization_id: str, environment_id: str) -> ConnectionTarget:
        """Revalidate a ticket-bound target without carrying an actor or DB session."""
        async with short_session(self._sessions) as session:
            row = await session.get(EnvironmentRecord, environment_id)
            if row is None or row.organization_id != organization_id:
                raise environment_not_found()
            provider = await session.get(EnvironmentProviderRecord, row.provider_id)
            target = self._target(row, provider)
            target.require_eligible()
            return target

    def _target(self, row: EnvironmentRecord, provider: EnvironmentProviderRecord | None) -> ConnectionTarget:
        if (
            row.ownership != "external"
            or row.template_revision_id is not None
            or row.status == "deleted"
            or provider is None
            or provider.type != WEBSOCKET_PROVIDER_KEY
            or provider.organization_id != row.organization_id
            or provider.workspace_id not in (None, row.workspace_id)
        ):
            raise invalid_environment("Environment is not an eligible client WebSocket target")
        implementation = self._environments.catalog.require(provider.type)
        configuration = EnvironmentConfiguration.model_validate(row.external_configuration)
        validated = implementation.validate_configuration(
            schema_version=configuration.configuration_schema_version, value=configuration.configuration
        )
        if not isinstance(validated, RemoteEnvdProviderConfiguration):
            raise TypeError("Client WebSocket Provider returned an invalid configuration")
        state = EnvironmentState.model_validate(row.state)
        native = decode_state(WEBSOCKET_PROVIDER_KEY, state)
        return ConnectionTarget(
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            environment_id=row.id,
            provider_id=provider.id,
            provider_enabled=provider.enabled,
            daemon_environment_id=native.daemon_environment_id,
            required_methods=REQUIRED_METHODS | frozenset(validated.required_methods),
            target_identity=row.target_identity,
            generation=row.generation,
            status=row.status,
            updated_at=assume_utc(row.updated_at),
            provider_updated_at=assume_utc(provider.updated_at),
            operation_generation=row.operation_generation,
            operation_id=row.operation_id,
        )

    async def publish(
        self,
        target: ConnectionTarget,
        status: Literal["running", "unavailable"],
        *,
        publication_id: str,
    ) -> bool:
        """CAS a captured observation; retry the same ID after an uncertain commit.

        Lifecycle claims and connection observations serialize on the Environment
        row. An in-flight lifecycle operation must resolve through its own owner.
        No connection observation changes the backing identity or generation.
        """
        details = {"status": status, "operation_generation": target.operation_generation}
        async with transaction(self._sessions) as session:
            row = await session.get(EnvironmentRecord, target.environment_id, with_for_update=True)
            receipt = await session.get(SecurityAuditRecord, publication_id)
            if receipt is not None:
                if (
                    receipt.action != "environment.connection_observation"
                    or receipt.resource_id != target.environment_id
                    or receipt.organization_id != target.organization_id
                    or receipt.details != details
                ):
                    raise ValueError("Connection publication receipt does not match its observation")
                return True
            if row is None or row.operation_id is not None:
                return False
            provider = await session.get(EnvironmentProviderRecord, row.provider_id, with_for_update=True)
            if (
                provider is None
                or provider.type != WEBSOCKET_PROVIDER_KEY
                or row.status == "deleted"
                or self._target(row, provider) != target
                or (status == "running" and not target.provider_enabled)
            ):
                return False
            now = utc_now()
            row.status = status
            row.operation_generation += 1
            row.updated_at = next_updated_at(row.updated_at, now)
            row.last_error = None if status == "running" else {"code": "environment_unavailable"}
            session.add(
                security_audit_record(
                    audit_id=publication_id,
                    actor=SystemAuditActor(request_id=None),
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    action="environment.connection_observation",
                    resource_type="environment",
                    resource_id=row.id,
                    outcome="success",
                    occurred_at=now,
                    details=details,
                )
            )
            return True

    async def reconciliation_batch(self, *, after_id: str = "", limit: int = 100) -> tuple[ConnectionTarget, ...]:
        """Capture a bounded keyset page before any Redis observation starts."""
        if not 1 <= limit <= 1000:
            raise ValueError("Connection reconciliation batch must contain 1 to 1000 targets")
        async with short_session(self._sessions) as session:
            rows = await session.execute(
                select(EnvironmentRecord, EnvironmentProviderRecord)
                .join(EnvironmentProviderRecord, EnvironmentRecord.provider_id == EnvironmentProviderRecord.id)
                .where(
                    EnvironmentRecord.id > after_id,
                    EnvironmentRecord.ownership == "external",
                    EnvironmentRecord.status.in_(("running", "unavailable")),
                    EnvironmentRecord.operation_id.is_(None),
                    EnvironmentProviderRecord.type == WEBSOCKET_PROVIDER_KEY,
                )
                .order_by(EnvironmentRecord.id)
                .limit(limit)
            )
            return tuple(self._target(row, provider) for row, provider in rows.tuples())
