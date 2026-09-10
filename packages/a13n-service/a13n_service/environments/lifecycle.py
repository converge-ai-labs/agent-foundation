"""Fenced Environment lifecycle operations with no database scope across Provider I/O."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from a13n_environment import Environment as OperationEnvironment
from a13n_environment import (
    EnvironmentError,
    EnvironmentProviderCatalog,
    EnvironmentProviderError,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentState,
)
from a13n_environment.management import ProviderRuntimeContext
from a13n_logging import exception_details, get_logger
from anyio import fail_after
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.credentials import CredentialSnapshot
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.authorization import (
    WorkspaceAction,
    authorize_persisted_agent_principal_actions,
    authorize_persisted_workspace_principal_action,
)
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.ids import new_object_id
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .capacity import DEFAULT_CAPACITY_LIMITS, CapacityLimits
from .configuration import load_configuration
from .domain import EnvironmentConfiguration, EnvironmentStatus, JsonObject, TemplateConfiguration, retention_action
from .errors import is_target_identity_conflict
from .identity import target_identity as scoped_target_identity
from .models import (
    EnvironmentCommandRecord,
    EnvironmentProviderRecord,
    EnvironmentRecord,
)
from .policy import FAILURE_BACKOFF, RENEWAL_MARGIN
from .retention import has_active_use, refresh_retention
from .scheduling import next_maintenance

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext

Action = Literal["prepare", "reconcile", "stop", "delete", "keepalive"]
logger = get_logger(__name__)


class EnvironmentOperationBusy(RuntimeError):
    """Conflicting lifecycle work has not reached a known outcome."""


@dataclass(frozen=True, slots=True)
class LifecycleOperation:
    environment_id: str
    provider_type: str
    provider_configuration: JsonObject
    credential: CredentialSnapshot
    configuration: EnvironmentConfiguration | TemplateConfiguration
    state: EnvironmentState | None
    operation_id: str
    fence: int
    owner: str
    action: Action
    previous_status: str
    run_id: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    environment: OperationEnvironment
    generation: int


class EnvironmentLifecycle:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        catalog: EnvironmentProviderCatalog,
        protector: SecretProtector,
        storage_root: Path,
        *,
        timeout_seconds: float = 60,
        clock: Clock = utc_now,
        capacity: CapacityLimits = DEFAULT_CAPACITY_LIMITS,
    ) -> None:
        self.sessions = sessions
        self.catalog = catalog
        self.protector = protector
        self.storage_root = storage_root
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.capacity = capacity

    @property
    def lease_duration(self) -> timedelta:
        return timedelta(seconds=self.timeout_seconds + 10)

    async def acquire(
        self, environment_id: str, action: Action, *, attempt: AttemptContext | None = None
    ) -> LifecycleOperation:
        from a13n_service.interactions.attempts import lock_attempt_authority

        if action == "prepare" and attempt is None:
            raise ValueError("Run preparation requires current Attempt authority")
        now = assume_utc(self.clock())
        async with transaction(self.sessions) as session:
            run = None
            if attempt is not None:
                run, _, _ = await lock_attempt_authority(session, attempt, now)
                if run.environment_id != environment_id:
                    raise ValueError("Environment is not the Run's accepted selection")
                await self.capacity.lock_workspace(session, environment_id)
            row = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.id == environment_id).with_for_update()
            )
            if row is None:
                raise ValueError("Environment is unavailable")
            provider = await session.get(EnvironmentProviderRecord, row.provider_id)
            if provider is None:
                raise ValueError("Environment Provider is unavailable")
            if provider.configuration.get("host_id", socket.gethostname()) != socket.gethostname():
                raise ValueError("Environment backend belongs to another host")
            if run is not None:
                await authorize_persisted_agent_principal_actions(
                    session,
                    principal=run.to_resource().authority_principal,
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    agent_id=run.agent_id,
                    actions=frozenset({WorkspaceAction.environment_use, WorkspaceAction.agent_invoke}),
                )
                if not provider.enabled:
                    raise ValueError("Environment Provider is disabled")
            resuming_operation = row.operation_id is not None
            if row.operation_id is not None:
                if row.operation_expires_at is not None and assume_utc(row.operation_expires_at) > now:
                    raise EnvironmentOperationBusy("Environment lifecycle operation is in progress")
                if row.operation_action != action and not (action == "reconcile" and row.operation_action == "prepare"):
                    raise EnvironmentOperationBusy("The preceding Environment operation must be reconciled first")
            else:
                if action == "reconcile":
                    raise ValueError("No abandoned preparation to reconcile")
                row.operation_id = new_object_id("envop")
                row.operation_action = action
            if action in {"stop", "delete"}:
                if row.ownership != "managed" or await has_active_use(session, row.id):
                    raise ValueError("Environment cannot be stopped or deleted while in use or externally owned")
            previous_status = row.status
            if run is not None:
                await self.capacity.admit(session, row)
                run.environment_use_started_at = run.environment_use_started_at or now
                if row.retention_condition != "active":
                    row.retention_condition = "active"
                    row.condition_since = now
            row.operation_generation += 1
            owner = new_object_id("envowner")
            row.operation_owner = owner
            row.operation_expires_at = now + self.lease_duration
            row.next_maintenance_at = now
            configuration = await load_configuration(session, row)
            if action in {"stop", "delete"} and not resuming_operation:
                assert isinstance(configuration, TemplateConfiguration)
                condition = await refresh_retention(session, row, now)
                due = retention_action(
                    configuration.retention,
                    condition=condition,
                    since=assume_utc(row.condition_since),
                    status=EnvironmentStatus(row.status),
                    now=now,
                )
                if due != action:
                    raise RuntimeError("Environment retention deadline changed")
            return LifecycleOperation(
                row.id,
                provider.type,
                provider.configuration,
                provider.credential_snapshot(),
                configuration,
                EnvironmentState.model_validate(row.state) if row.state else None,
                row.operation_id,
                row.operation_generation,
                owner,
                action,
                previous_status,
                attempt.run_id if attempt else None,
                attempt.run_attempt_id if attempt else None,
            )

    async def validate_use(self, attempt: AttemptContext, environment_id: str) -> None:
        from a13n_service.interactions.attempts import lock_attempt_authority

        async with transaction(self.sessions) as session:
            run, _, _ = await lock_attempt_authority(session, attempt, assume_utc(self.clock()))
            row = await session.get(EnvironmentRecord, environment_id)
            if row is None or run.environment_id != environment_id:
                raise ValueError("Environment is not the Run selection")
            provider = await session.get(EnvironmentProviderRecord, row.provider_id)
            if provider is None or not provider.enabled:
                raise ValueError("Environment Provider is disabled")
            await authorize_persisted_agent_principal_actions(
                session,
                principal=run.to_resource().authority_principal,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                agent_id=run.agent_id,
                actions=frozenset({WorkspaceAction.environment_use, WorkspaceAction.agent_invoke}),
            )

    async def validate_operation(self, operation: LifecycleOperation) -> None:
        """Reject a suspended owner before dispatching a new native lifecycle effect."""
        now = assume_utc(self.clock())
        async with transaction(self.sessions) as session:
            row = await session.get(EnvironmentRecord, operation.environment_id, with_for_update=True)
            if row is None or (row.operation_id, row.operation_generation, row.operation_owner) != (
                operation.operation_id,
                operation.fence,
                operation.owner,
            ):
                raise RuntimeError("Environment lifecycle authority changed")
            if row.operation_expires_at is None or assume_utc(row.operation_expires_at) <= now:
                raise RuntimeError("Environment lifecycle authority expired")
            if operation.action not in {"stop", "delete"}:
                return
            if await has_active_use(session, row.id):
                raise ValueError("Environment cannot be stopped or deleted while in use")
            command = await session.get(EnvironmentCommandRecord, operation.operation_id)
            if command is not None:
                await authorize_persisted_workspace_principal_action(
                    session,
                    principal=PrincipalRef(
                        principal_type=PrincipalType(command.principal_type), principal_id=command.principal_id
                    ),
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    action=WorkspaceAction.environment_manage,
                )

    async def construct(self, operation: LifecycleOperation) -> OperationEnvironment:
        provider = self.catalog.require(operation.provider_type)
        credential = None
        if provider.credential_model is not None:
            credential = provider.credential_model.model_validate_json(operation.credential.decrypt(self.protector))
        configuration = provider.validate_configuration(
            schema_version=operation.configuration.configuration_schema_version,
            value=operation.configuration.configuration,
        )
        runtime = await provider.create_runtime(
            configuration=provider.provider_configuration_model.model_validate(operation.provider_configuration),
            credential=credential,
            context=ProviderRuntimeContext(
                operation.environment_id,
                operation.operation_id,
                self.storage_root,
                isinstance(operation.configuration, TemplateConfiguration),
            ),
        )
        return provider.create_environment(
            configuration=configuration, environment_id=operation.environment_id, state=operation.state, runtime=runtime
        )

    async def execute(
        self, operation: LifecycleOperation, *, recovering: OperationEnvironment | None = None
    ) -> LifecycleResult:
        if recovering is not None and operation.action != "prepare":
            raise ValueError("Only preparation can recover an existing operation scope")
        environment = recovering
        effect_known = False
        expires_at = None
        observation = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                if environment is None:
                    environment = await self.construct(operation)
                # Acquisition may have preceded a process suspension. Recheck
                # immediately before dispatch; publication separately fences the
                # result of I/O that was already in flight during a takeover.
                await self.validate_operation(operation)
                if operation.action == "reconcile":
                    observation = await environment.reconcile()
                elif operation.action == "prepare":
                    if recovering is None:
                        await environment.prepare()
                    else:
                        await environment.recover()
                elif operation.previous_status in {"unprepared", "deleted"} and operation.action in {"stop", "delete"}:
                    pass
                elif operation.action == "stop":
                    await environment.stop()
                elif operation.action == "delete":
                    await environment.destroy()
                else:
                    deadline = assume_utc(self.clock()) + environment.keepalive_horizon
                    retained_until = await environment.keepalive(deadline=deadline, operation_id=operation.operation_id)
                    effect_known = True
                    expires_at = retained_until
                    if retained_until is None or assume_utc(retained_until) < deadline:
                        raise EnvironmentError(
                            "Provider did not confirm the required retention deadline",
                            code="environment_keepalive_failed",
                        )
                effect_known = True
                generation = await self.publish(operation, environment, observation=observation, expires_at=expires_at)
            return LifecycleResult(environment, generation)
        except BaseException as error:
            if isinstance(error, Exception):
                logger.warning(
                    "environment_lifecycle_failed",
                    extra={
                        "environment_id": operation.environment_id,
                        "operation_id": operation.operation_id,
                        "action": operation.action,
                        "provider_type": operation.provider_type,
                        "run_id": operation.run_id,
                        "attempt_id": operation.attempt_id,
                        "exception_chain": exception_details(error),
                    },
                )
            if is_target_identity_conflict(error):
                error = EnvironmentError(
                    "This backend target already has an Environment owner.", code="environment_target_conflict"
                )
            # A known target must survive readiness failure or cancellation. Unknown
            # operations retain their identity for reconciliation, never a new create.
            try:
                with fail_after(10, shield=True):
                    await self.publish(
                        operation,
                        environment,
                        error=error,
                        effect_known=effect_known,
                        observation=observation,
                        expires_at=expires_at,
                    )
            except BaseException as publication_error:
                error.add_note(f"Lifecycle failure publication also failed: {publication_error!r}")
            finally:
                if environment is not None:
                    try:
                        with fail_after(10, shield=True):
                            await environment.close()
                    except BaseException as cleanup_error:
                        error.add_note(f"Environment cleanup also failed: {cleanup_error!r}")
            raise error

    async def publish(
        self,
        operation: LifecycleOperation,
        environment: OperationEnvironment | None,
        *,
        error: BaseException | None = None,
        effect_known: bool = False,
        expires_at: datetime | None = None,
        observation: Literal["running", "stopped", "absent"] | None = None,
    ) -> int:
        succeeded = error is None
        # Construction cannot dispatch target work. Provider errors explicitly distinguish
        # a known failure from an operation whose external effect remains unknown.
        resolved = (
            succeeded
            or environment is None
            or effect_known
            or (
                isinstance(error, EnvironmentProviderError)
                and error.certainty != EnvironmentProviderOutcomeCertainty.UNKNOWN
            )
        )
        state = environment.dump_state() if environment is not None else operation.state
        provider = self.catalog.require(operation.provider_type)
        identity = None
        target_conflict = isinstance(error, EnvironmentError) and error.code == "environment_target_conflict"
        if (
            not target_conflict
            and observation != "absent"
            and (state is not None or succeeded)
            and operation.action in {"prepare", "reconcile"}
        ):
            configuration = provider.validate_configuration(
                schema_version=operation.configuration.configuration_schema_version,
                value=operation.configuration.configuration,
            )
            identity = scoped_target_identity(
                provider,
                operation.provider_configuration,
                provider.target_identity(configuration=configuration, state=state),
            )
        now = assume_utc(self.clock())
        async with transaction(self.sessions) as session:
            row = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.id == operation.environment_id).with_for_update()
            )
            if row is None or (row.operation_id, row.operation_generation, row.operation_owner) != (
                operation.operation_id,
                operation.fence,
                operation.owner,
            ):
                raise RuntimeError("Environment lifecycle authority changed")
            if state is not None:
                row.state = state.model_dump(mode="json")
            if identity is not None and identity != row.target_identity:
                row.generation += 1
                row.target_identity = identity
                row.expires_at = None
            if operation.action == "keepalive" and expires_at is not None:
                row.expires_at = expires_at
            # Preserve a confirmed observation even if its first publication failed.
            if succeeded or (observation is not None and not target_conflict):
                if operation.action == "prepare" and row.generation == 0:
                    row.generation = 1
                if operation.action == "delete" or observation == "absent":
                    row.status, row.state, row.target_identity = "deleted", None, None
                    row.expires_at = None
                elif operation.action == "stop":
                    row.status = (
                        operation.previous_status
                        if operation.previous_status in {"unprepared", "deleted"}
                        else "stopped"
                    )
                    row.expires_at = None
                elif operation.action == "prepare":
                    row.status = "running"
                elif operation.action == "reconcile":
                    assert observation is not None
                    row.status = observation
            elif operation.action in {"prepare", "reconcile"}:
                row.status = (
                    operation.previous_status
                    if resolved and state is None and operation.previous_status in {"unprepared", "deleted"}
                    else "unavailable"
                )
            row.last_error = (
                None
                if succeeded
                else {"code": "environment_operation_failed" if resolved else "environment_operation_unresolved"}
            )
            if resolved:
                command = await session.get(EnvironmentCommandRecord, operation.operation_id)
                if command is not None:
                    command.status = "completed" if succeeded else "failed"
                    command.completed_at = now
                row.operation_id = row.operation_action = row.operation_owner = None
                row.operation_expires_at = None
            row.updated_at = now
            deadline = (
                next_maintenance(row, operation.configuration, requires_keepalive=provider.requires_keepalive, now=now)
                if isinstance(operation.configuration, TemplateConfiguration)
                else None
            )
            row.next_maintenance_at = max(deadline, now + FAILURE_BACKOFF) if not succeeded and deadline else deadline
            session.add(
                security_audit_record(
                    audit_id=new_object_id("aud"),
                    actor=SystemAuditActor(request_id=None),
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    action=f"environment.{operation.action}",
                    resource_type="environment",
                    resource_id=row.id,
                    outcome="success" if succeeded else "failure",
                    occurred_at=now,
                    details={
                        "operation_id": operation.operation_id,
                        "generation": row.generation,
                        "run_id": operation.run_id,
                        "run_attempt_id": operation.attempt_id,
                        "status": row.status,
                        "outcome_known": resolved,
                    },
                )
            )

            return row.generation

    async def maintain(self, environment_id: str) -> None:
        action: Action | None = None
        now = assume_utc(self.clock())
        async with transaction(self.sessions) as session:
            row = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.id == environment_id).with_for_update()
            )
            if row is None or row.ownership != "managed":
                return
            configuration = await load_configuration(session, row)
            assert isinstance(configuration, TemplateConfiguration)
            provider = await session.get(EnvironmentProviderRecord, row.provider_id)
            if provider is None:
                raise ValueError("Environment Provider is unavailable")
            implementation = self.catalog.require(provider.type)
            condition = await refresh_retention(session, row, now)
            if row.operation_id is not None:
                if row.operation_expires_at is None or assume_utc(row.operation_expires_at) <= now:
                    action = abandoned_action(row.operation_action)
            else:
                action = retention_action(
                    configuration.retention,
                    condition=condition,
                    since=assume_utc(row.condition_since),
                    status=EnvironmentStatus(row.status),
                    now=now,
                )
                if (
                    action is None
                    and row.status == "running"
                    and implementation.requires_keepalive
                    and (row.expires_at is None or assume_utc(row.expires_at) <= now + RENEWAL_MARGIN)
                ):
                    action = "keepalive"
            row.next_maintenance_at = next_maintenance(
                row, configuration, requires_keepalive=implementation.requires_keepalive, now=now
            )
        if action is not None:
            operation = await self.acquire(environment_id, action)
            result = await self.execute(operation)
            await result.environment.close()


def abandoned_action(action: str | None) -> Action:
    match action:
        case "prepare" | "reconcile":
            return "reconcile"
        case "stop" | "delete" | "keepalive":
            return action
        case _:
            raise ValueError("Invalid persisted Environment lifecycle action")
