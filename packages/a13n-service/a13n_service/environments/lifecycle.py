"""Fenced Environment lifecycle operations with no database scope across Provider I/O."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog
from a13n_harness.providers.environment.errors import EnvironmentProviderError, EnvironmentProviderOutcomeCertainty
from a13n_harness.providers.environment.management import Environment as OperationEnvironment
from a13n_harness.providers.environment.models import EnvironmentError, EnvironmentState
from a13n_logging import exception_details, get_logger
from anyio import fail_after
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.credentials import CredentialSnapshot
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.authorization import (
    WorkspaceAction,
    authorize_persisted_workspace_principal_action,
)
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import is_database_unavailable, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .capacity import DEFAULT_CAPACITY_LIMITS, CapacityLimits
from .configuration import load_configuration
from .domain import EnvironmentConfiguration, EnvironmentStatus, JsonObject, TemplateConfiguration, retention_action
from .errors import is_target_identity_conflict
from .identity import target_identity as scoped_target_identity
from .local_directory import instance_configuration, managed_local_directory
from .models import (
    EnvironmentCommandRecord,
    EnvironmentProviderRecord,
    EnvironmentRecord,
)
from .policy import FAILURE_BACKOFF, RENEWAL_MARGIN
from .retention import has_active_use, refresh_retention
from .run_use import lock_run_environment_use, mark_run_environment_use
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
    previous_outcome_unknown: bool = False


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """Provider evidence captured before publication; storage errors never alter it."""

    certainty: EnvironmentProviderOutcomeCertainty
    state: EnvironmentState | None
    observation: EnvironmentStatus | None = None
    expires_at: datetime | None = None
    error: BaseException | None = field(default=None, repr=False)
    publication_id: str = field(default_factory=lambda: new_object_id("aud"))

    @property
    def succeeded(self) -> bool:
        return self.error is None

    @property
    def resolved(self) -> bool:
        return self.certainty != EnvironmentProviderOutcomeCertainty.UNKNOWN


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
        *,
        timeout_seconds: float = 60,
        clock: Clock = utc_now,
        capacity: CapacityLimits = DEFAULT_CAPACITY_LIMITS,
    ) -> None:
        self.sessions = sessions
        self.catalog = catalog
        self.protector = protector
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.capacity = capacity

    @property
    def lease_duration(self) -> timedelta:
        return timedelta(seconds=self.timeout_seconds + 10)

    async def acquire_preparation(
        self, environment_id: str, *, attempt: AttemptContext, mount_name: str = "workspace"
    ) -> LifecycleOperation:
        now = assume_utc(self.clock())
        async with transaction(self.sessions) as session:
            binding, row, provider = await lock_run_environment_use(
                session, environment_id, attempt, self.capacity, now, mount_name=mount_name
            )
            if row.operation_id is not None:
                if row.operation_expires_at is not None and assume_utc(row.operation_expires_at) > now:
                    raise EnvironmentOperationBusy("Environment lifecycle operation is in progress")
                if row.operation_action != "prepare":
                    raise EnvironmentOperationBusy("The preceding Environment operation must be reconciled first")
            await self.capacity.admit(session, row)
            mark_run_environment_use(binding, row, now)
            configuration = await load_configuration(session, row)
            return self._claim(row, provider, configuration, "prepare", now, attempt=attempt)

    def _claim(
        self,
        row: EnvironmentRecord,
        provider: EnvironmentProviderRecord,
        configuration: EnvironmentConfiguration | TemplateConfiguration,
        action: Action,
        now: datetime,
        *,
        attempt: AttemptContext | None = None,
    ) -> LifecycleOperation:
        """Record ownership after the caller decides eligibility under the row lock."""
        # Validate the snapshot before changing ownership, so a planning failure
        # can back off without leaving a partially claimed operation behind.
        operation = LifecycleOperation(
            environment_id=row.id,
            provider_type=provider.type,
            provider_configuration=provider.configuration,
            credential=provider.credential_snapshot(),
            configuration=configuration,
            state=EnvironmentState.model_validate(row.state) if row.state else None,
            operation_id=row.operation_id or new_object_id("envop"),
            fence=row.operation_generation + 1,
            owner=new_object_id("envowner"),
            action=action,
            previous_status=row.status,
            run_id=attempt.run_id if attempt else None,
            attempt_id=attempt.run_attempt_id if attempt else None,
            # An accepted manual command has no lease. A previously claimed
            # operation may have dispatched even without a saved error.
            previous_outcome_unknown=row.operation_id is not None and row.operation_expires_at is not None,
        )
        if row.operation_id is None:
            row.operation_id, row.operation_action = operation.operation_id, action
        row.operation_generation = operation.fence
        row.operation_owner = operation.owner
        row.operation_expires_at = now + self.lease_duration
        row.next_maintenance_at = row.operation_expires_at
        return operation

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
        raw = operation.credential.decrypt(self.protector) if operation.credential.ciphertext is not None else None
        return await provider.create(
            instance_configuration(operation.provider_type, operation.environment_id, operation.configuration),
            schema_version=operation.configuration.configuration_schema_version,
            configuration=operation.provider_configuration,
            credential=json.loads(raw) if raw is not None else None,
            environment_id=operation.environment_id,
            operation_id=operation.operation_id,
            allow_create=isinstance(operation.configuration, TemplateConfiguration),
            state=operation.state,
        )

    async def execute(
        self, operation: LifecycleOperation, *, recovering: OperationEnvironment | None = None
    ) -> LifecycleResult:
        if recovering is not None and operation.action != "prepare":
            raise ValueError("Only preparation can recover an existing operation scope")
        environment = recovering
        try:
            dispatched = False
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    if environment is None:
                        environment = await self.construct(operation)
                    await self.validate_operation(operation)
                    dispatched = True
                    outcome = await self._dispatch(operation, environment, recovering=recovering is not None)
            except BaseException as error:
                certainty = EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED
                if dispatched:
                    certainty = (
                        error.certainty
                        if isinstance(error, EnvironmentProviderError)
                        else EnvironmentProviderOutcomeCertainty.UNKNOWN
                    )
                # Not dispatching this attempt cannot resolve an earlier owner's
                # unknown effect. Keep its identity until Provider evidence resolves it.
                if (
                    operation.previous_outcome_unknown
                    and certainty == EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED
                ):
                    certainty = EnvironmentProviderOutcomeCertainty.UNKNOWN
                outcome = LifecycleOutcome(
                    certainty=certainty,
                    state=environment.dump_state() if environment is not None else operation.state,
                    error=error,
                )

            execution_error = outcome.error
            try:
                generation, outcome = await self._publish_outcome(operation, outcome)
            except BaseException as publication_error:
                if isinstance(publication_error, asyncio.CancelledError):
                    raise
                if outcome.error is not None:
                    outcome.error.add_note(f"Lifecycle outcome publication also failed: {publication_error!r}")
                    raise outcome.error from publication_error
                raise
            if isinstance(execution_error, asyncio.CancelledError):
                raise execution_error
            if outcome.error is not None:
                raise outcome.error
            assert environment is not None
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
            if environment is not None:
                try:
                    with fail_after(10, shield=True):
                        await environment.close()
                except BaseException as cleanup_error:
                    error.add_note(f"Environment cleanup also failed: {cleanup_error!r}")
            raise

    async def _dispatch(
        self, operation: LifecycleOperation, environment: OperationEnvironment, *, recovering: bool
    ) -> LifecycleOutcome:
        directory = managed_local_directory(operation.provider_type, operation.environment_id, operation.configuration)
        observation = None
        expires_at = None
        error = None
        if operation.action == "reconcile":
            observed = await environment.reconcile()
            observation = EnvironmentStatus.deleted if observed == "absent" else EnvironmentStatus(observed)
        elif operation.action == "prepare":
            if directory is not None:
                await directory.create()
            if recovering:
                await environment.recover()
            else:
                await environment.prepare()
            observation = EnvironmentStatus.running
        elif operation.action == "stop":
            observation = EnvironmentStatus(operation.previous_status)
            if observation not in {EnvironmentStatus.unprepared, EnvironmentStatus.deleted}:
                await environment.stop()
                observation = EnvironmentStatus.stopped
        elif operation.action == "delete":
            if directory is not None:
                await directory.delete()
            if operation.previous_status not in {"unprepared", "deleted"}:
                await environment.destroy()
            observation = EnvironmentStatus.deleted
        else:
            deadline = assume_utc(self.clock()) + environment.keepalive_horizon
            expires_at = await environment.keepalive(deadline=deadline, operation_id=operation.operation_id)
            if expires_at is None or assume_utc(expires_at) < deadline:
                error = EnvironmentError(
                    "Provider did not confirm the required retention deadline",
                    code="environment_keepalive_failed",
                )
        return LifecycleOutcome(
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            state=environment.dump_state(),
            observation=observation,
            expires_at=expires_at,
            error=error,
        )

    async def _publish_outcome(
        self, operation: LifecycleOperation, outcome: LifecycleOutcome
    ) -> tuple[int, LifecycleOutcome]:
        """Retry only publication, with one bounded recovery attempt and stable evidence."""
        with fail_after(10, shield=True):
            try:
                return await self.publish(operation, outcome), outcome
            except Exception as error:
                if is_target_identity_conflict(error):
                    # A rejected ownership claim is a domain conflict, not a
                    # transient write failure. Retain state without adopting the target.
                    outcome = replace(
                        outcome,
                        observation=None,
                        error=EnvironmentError(
                            "This backend target already has an Environment owner.", code="environment_target_conflict"
                        ),
                    )
                elif not is_database_unavailable(error):
                    raise
                logger.warning(
                    "environment_outcome_publication_retry",
                    extra={
                        "environment_id": operation.environment_id,
                        "operation_id": operation.operation_id,
                        "publication_id": outcome.publication_id,
                        "error_type": type(error).__name__,
                    },
                )
            return await self.publish(operation, outcome), outcome

    async def publish(self, operation: LifecycleOperation, outcome: LifecycleOutcome) -> int:
        """Atomically persist captured evidence, including a receipt for ambiguous commits."""
        state = outcome.state
        provider = self.catalog.require(operation.provider_type)
        identity = None
        target_conflict = (
            isinstance(outcome.error, EnvironmentError) and outcome.error.code == "environment_target_conflict"
        )
        if (
            not target_conflict
            and outcome.observation != EnvironmentStatus.deleted
            and (state is not None or outcome.succeeded)
            and operation.action in {"prepare", "reconcile"}
        ):
            configuration = provider.validate_environment(
                schema_version=operation.configuration.configuration_schema_version,
                value=instance_configuration(
                    operation.provider_type, operation.environment_id, operation.configuration
                ),
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
            receipt = await session.get(SecurityAuditRecord, outcome.publication_id)
            if receipt is not None:
                details = receipt.details or {}
                if (
                    receipt.resource_id != operation.environment_id
                    or details.get("operation_id") != operation.operation_id
                    or details.get("operation_generation") != operation.fence
                ):
                    raise RuntimeError("Environment publication receipt does not match its operation")
                generation = details.get("generation")
                if not isinstance(generation, int):
                    raise RuntimeError("Environment publication receipt has no generation")
                return generation
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
            if operation.action == "keepalive" and outcome.expires_at is not None:
                row.expires_at = outcome.expires_at
            if outcome.observation is not None:
                row.status = outcome.observation.value
                if outcome.observation == EnvironmentStatus.deleted:
                    row.state = row.target_identity = None
                    row.expires_at = None
                elif operation.action == "stop":
                    row.expires_at = None
                elif operation.action == "prepare" and row.generation == 0:
                    row.generation = 1
            elif operation.action in {"prepare", "reconcile"}:
                row.status = (
                    operation.previous_status
                    if outcome.resolved and state is None and operation.previous_status in {"unprepared", "deleted"}
                    else "unavailable"
                )
            row.last_error = (
                None
                if outcome.succeeded
                else {
                    "code": "environment_operation_failed" if outcome.resolved else "environment_operation_unresolved"
                }
            )
            if outcome.resolved:
                command = await session.get(EnvironmentCommandRecord, operation.operation_id)
                if command is not None:
                    command.status = "completed" if outcome.succeeded else "failed"
                    command.completed_at = now
                row.operation_id = row.operation_action = row.operation_owner = None
                row.operation_expires_at = None
            row.updated_at = now
            deadline = (
                next_maintenance(row, operation.configuration, requires_keepalive=provider.requires_keepalive, now=now)
                if isinstance(operation.configuration, TemplateConfiguration)
                else None
            )
            row.next_maintenance_at = (
                max(deadline, now + FAILURE_BACKOFF) if not outcome.succeeded and deadline else deadline
            )
            session.add(
                security_audit_record(
                    audit_id=outcome.publication_id,
                    actor=SystemAuditActor(request_id=None),
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    action=f"environment.{operation.action}",
                    resource_type="environment",
                    resource_id=row.id,
                    outcome="success" if outcome.succeeded else "failure",
                    occurred_at=now,
                    details={
                        "operation_id": operation.operation_id,
                        "generation": row.generation,
                        "run_id": operation.run_id,
                        "run_attempt_id": operation.attempt_id,
                        "status": row.status,
                        "outcome_known": outcome.resolved,
                        "operation_generation": operation.fence,
                    },
                )
            )

            return row.generation

    async def acquire_maintenance(
        self, environment_id: str, *, cutoff: datetime | None = None
    ) -> LifecycleOperation | None:
        """Decide and claim under one lock; a sweep also rechecks its fixed cutoff."""
        now = assume_utc(self.clock())
        failure: Exception | None = None
        async with transaction(self.sessions) as session:
            query = select(EnvironmentRecord).where(
                EnvironmentRecord.id == environment_id,
                EnvironmentRecord.ownership == "managed",
            )
            if cutoff is not None:
                query = query.where(EnvironmentRecord.next_maintenance_at <= assume_utc(cutoff))
            row = await session.scalar(query.with_for_update(of=EnvironmentRecord, skip_locked=True))
            if row is None:
                return None
            if (
                row.operation_id is not None
                and row.operation_expires_at is not None
                and assume_utc(row.operation_expires_at) > now
            ):
                row.next_maintenance_at = max(
                    assume_utc(row.next_maintenance_at) if row.next_maintenance_at else now,
                    assume_utc(row.operation_expires_at),
                )
                return None
            try:
                configuration = await load_configuration(session, row)
                assert isinstance(configuration, TemplateConfiguration)
                provider = await session.get(EnvironmentProviderRecord, row.provider_id)
                if provider is None:
                    raise ValueError("Environment Provider is unavailable")
                implementation = self.catalog.require(provider.type)
                condition = await refresh_retention(session, row, now)
                if row.operation_id is not None:
                    action = abandoned_action(row.operation_action)
                    if action in {"stop", "delete"} and condition == "active":
                        raise ValueError("Environment cannot be stopped or deleted while in use")
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
                if action is not None:
                    return self._claim(row, provider, configuration, action, now)
                row.next_maintenance_at = next_maintenance(
                    row, configuration, requires_keepalive=implementation.requires_keepalive, now=now
                )
            except SQLAlchemyError:
                # Database failures roll back the whole decision; no deadline or
                # ownership is reliable until the transaction commits.
                raise
            except Exception as error:
                row.next_maintenance_at = now + FAILURE_BACKOFF
                failure = error
        if failure is not None:
            raise failure
        return None

    async def maintain(self, environment_id: str, *, cutoff: datetime | None = None) -> None:
        operation = await self.acquire_maintenance(environment_id, cutoff=cutoff)
        if operation is not None:
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
