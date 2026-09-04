"""Fenced Environment lifecycle operations with no database scope across Provider I/O."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from a13n_environment_provider import Environment as OperationEnvironment
from a13n_environment_provider import (
    EnvironmentError,
    EnvironmentProviderCatalog,
    EnvironmentProviderError,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentState,
)
from a13n_environment_provider.management import ProviderRuntimeContext
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
from a13n_service.interactions.attempts import AttemptContext, lock_attempt_lease
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import EnvironmentStatus, JsonObject, TemplateConfiguration, retention_action
from .models import (
    EnvironmentCommandRecord,
    EnvironmentProviderRecord,
    EnvironmentRecord,
    EnvironmentTemplateRevisionRecord,
)

Action = Literal["prepare", "stop", "delete", "keepalive"]


class EnvironmentOperationBusy(RuntimeError):
    """Conflicting lifecycle work has not reached a known outcome."""


@dataclass(frozen=True, slots=True)
class LifecycleOperation:
    environment_id: str
    provider_type: str
    provider_configuration: JsonObject
    credential: CredentialSnapshot
    recipe: TemplateConfiguration
    state: EnvironmentState | None
    managed: bool
    operation_id: str
    fence: int
    owner: str
    action: Action
    target_identity: str | None
    run_id: str | None = None
    attempt_id: str | None = None


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
    ) -> None:
        self.sessions = sessions
        self.catalog = catalog
        self.protector = protector
        self.storage_root = storage_root
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    async def acquire(
        self, environment_id: str, action: Action, *, attempt: AttemptContext | None = None
    ) -> LifecycleOperation:
        if action == "prepare" and attempt is None:
            raise ValueError("Run preparation requires current Attempt authority")
        now = assume_utc(self.clock())
        async with transaction(self.sessions) as session:
            run = None
            if attempt is not None:
                run, _, _ = await lock_attempt_lease(session, attempt, now)
                if run.environment_id != environment_id:
                    raise ValueError("Environment is not the Run's accepted selection")
            row = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.id == environment_id).with_for_update()
            )
            if row is None:
                raise ValueError("Environment is unavailable")
            provider = await session.get(EnvironmentProviderRecord, row.provider_id)
            if provider is None:
                raise ValueError("Environment Provider is unavailable")
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
            command = await session.get(EnvironmentCommandRecord, row.operation_id) if row.operation_id else None
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
            resuming_operation = row.operation_id is not None
            if row.operation_id is not None:
                if row.operation_expires_at is not None and assume_utc(row.operation_expires_at) > now:
                    raise EnvironmentOperationBusy("Environment lifecycle operation is in progress")
                if row.operation_action != action:
                    raise EnvironmentOperationBusy("The preceding Environment operation must be reconciled first")
            else:
                row.operation_id = new_object_id("envop")
                row.operation_action = action
            if action in {"stop", "delete"}:
                if row.ownership != "managed" or await has_active_use(session, row.id):
                    raise ValueError("Environment cannot be stopped or deleted while in use or externally owned")
            if run is not None:
                run.environment_use_started_at = run.environment_use_started_at or now
                row.retention_condition = "active"
                row.condition_since = now
            row.operation_generation += 1
            row.operation_owner = new_object_id("envowner")
            row.operation_expires_at = now + timedelta(seconds=self.timeout_seconds + 10)
            row.next_maintenance_at = now
            if row.template_revision_id is not None:
                revision = await session.get(EnvironmentTemplateRevisionRecord, row.template_revision_id)
                if revision is None:
                    raise ValueError("Environment recipe is unavailable")
                recipe = TemplateConfiguration.model_validate(revision.recipe)
            else:
                recipe = TemplateConfiguration.model_validate(
                    {
                        **(row.external_configuration or {}),
                        "provider_id": provider.id,
                        "access": row.access,
                        "retention": {"idle": {"stop_after": None, "delete_after": None}},
                    }
                )
            if action in {"stop", "delete"} and not resuming_operation:
                condition, changed_at = await _condition(session, row, now)
                if condition != row.retention_condition:
                    row.retention_condition, row.condition_since = condition, changed_at
                due = retention_action(
                    recipe.retention,
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
                recipe,
                EnvironmentState.model_validate(row.state) if row.state else None,
                row.ownership == "managed",
                row.operation_id,
                row.operation_generation,
                row.operation_owner,
                action,
                row.target_identity,
                attempt.run_id if attempt else None,
                attempt.run_attempt_id if attempt else None,
            )

    async def validate_use(self, attempt: AttemptContext, environment_id: str) -> None:
        async with transaction(self.sessions) as session:
            run, _, _ = await lock_attempt_lease(session, attempt, assume_utc(self.clock()))
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

    async def construct(self, operation: LifecycleOperation) -> OperationEnvironment:
        provider = self.catalog.require(operation.provider_type)
        credential = None
        if provider.credential_model is not None:
            credential = provider.credential_model.model_validate_json(operation.credential.decrypt(self.protector))
        runtime = await provider.create_runtime(
            configuration=provider.provider_configuration_model.model_validate(operation.provider_configuration),
            credential=credential,
            context=ProviderRuntimeContext(
                operation.environment_id, operation.operation_id, self.storage_root, operation.managed
            ),
        )
        configuration = provider.validate_configuration(
            schema_version=operation.recipe.configuration_schema_version, value=operation.recipe.configuration
        )
        return provider.create_environment(
            configuration=configuration, environment_id=operation.environment_id, state=operation.state, runtime=runtime
        )

    async def execute(self, operation: LifecycleOperation) -> OperationEnvironment:
        environment = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                environment = await self.construct(operation)
                if operation.action == "prepare":
                    await environment.prepare()
                elif operation.state is None and operation.action in {"stop", "delete"}:
                    pass
                elif operation.action == "stop":
                    await environment.stop()
                elif operation.action == "delete":
                    await environment.destroy()
                else:
                    deadline = assume_utc(self.clock()) + timedelta(seconds=300)
                    retained_until = await environment.keepalive(deadline=deadline, operation_id=operation.operation_id)
                    if retained_until is None or assume_utc(retained_until) < deadline:
                        raise EnvironmentError(
                            "Provider did not confirm the required retention deadline",
                            code="environment_keepalive_failed",
                        )
                await self.publish(operation, environment)
            return environment
        except BaseException as error:
            # A known target must survive readiness failure or cancellation. Unknown
            # operations retain their identity for reconciliation, never a new create.
            if environment is not None:
                task = asyncio.create_task(self.publish(operation, environment, error=error))
                try:
                    await asyncio.shield(task)
                finally:
                    await environment.close()
            raise

    async def publish(
        self, operation: LifecycleOperation, environment: OperationEnvironment, *, error: BaseException | None = None
    ) -> None:
        succeeded = error is None
        resolved_preparation_failure = operation.action == "prepare" and (
            isinstance(error, EnvironmentError)
            or (
                isinstance(error, EnvironmentProviderError)
                and error.certainty != EnvironmentProviderOutcomeCertainty.UNKNOWN
            )
        )
        state = environment.dump_state()
        provider = self.catalog.require(operation.provider_type)
        configuration = provider.validate_configuration(
            schema_version=operation.recipe.configuration_schema_version, value=operation.recipe.configuration
        )
        identity = (
            provider.target_identity(configuration=configuration, state=state)
            if state is not None or succeeded
            else None
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
            if identity is not None and operation.action == "prepare" and identity != row.target_identity:
                row.generation += 1
                row.target_identity = identity
            if succeeded:
                if operation.action == "prepare" and row.generation == 0:
                    row.generation = 1
                if operation.action == "delete":
                    row.status = "deleted"
                    row.state = None
                    row.target_identity = None
                elif operation.action == "stop":
                    row.status = "stopped"
                elif operation.action == "prepare":
                    row.status = "running"
                command = await session.get(EnvironmentCommandRecord, operation.operation_id)
                if command is not None:
                    command.status = "completed"
                    command.completed_at = now
                row.operation_id = row.operation_action = row.operation_owner = None
                row.operation_expires_at = None
                row.last_error = None
            elif resolved_preparation_failure:
                row.status = "unavailable"
                row.operation_id = row.operation_action = row.operation_owner = None
                row.operation_expires_at = None
                row.last_error = {"code": "environment_preparation_failed"}
            else:
                row.last_error = {"code": "environment_operation_unresolved"}
            row.updated_at = now
            row.next_maintenance_at = now + timedelta(seconds=10)
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
                        "outcome_known": succeeded or resolved_preparation_failure,
                    },
                )
            )

    async def maintain(self, environment_id: str) -> None:
        action: Action | None = None
        now = assume_utc(self.clock())
        async with transaction(self.sessions) as session:
            row = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.id == environment_id).with_for_update()
            )
            if row is None or row.ownership != "managed":
                return
            revision = await session.get(EnvironmentTemplateRevisionRecord, row.template_revision_id)
            if revision is None:
                return
            recipe = revision.to_resource()
            condition, changed_at = await _condition(session, row, now)
            if condition != row.retention_condition:
                row.retention_condition = condition
                row.condition_since = changed_at
            if row.operation_id is not None:
                if row.operation_expires_at is not None and assume_utc(row.operation_expires_at) > now:
                    return
                if row.operation_action in {"stop", "delete", "keepalive"}:
                    action = (
                        "stop"
                        if row.operation_action == "stop"
                        else "delete"
                        if row.operation_action == "delete"
                        else "keepalive"
                    )
                # Preparation needs the current Run's authority, never a maintenance principal.
            else:
                action = retention_action(
                    recipe.retention,
                    condition=condition,
                    since=assume_utc(row.condition_since),
                    status=EnvironmentStatus(row.status),
                    now=now,
                )
                provider = await session.get(EnvironmentProviderRecord, row.provider_id)
                if (
                    action is None
                    and row.status == "running"
                    and provider is not None
                    and self.catalog.require(provider.type).requires_keepalive
                ):
                    action = "keepalive"
            row.next_maintenance_at = now + timedelta(seconds=10)
        if action is not None:
            operation = await self.acquire(environment_id, action)
            environment = await self.execute(operation)
            await environment.close()


async def has_active_use(session: AsyncSession, environment_id: str) -> bool:
    return (
        await session.scalar(
            select(RunRecord.id)
            .where(
                RunRecord.environment_id == environment_id,
                RunRecord.status == "running",
                RunRecord.environment_use_started_at.is_not(None),
            )
            .limit(1)
        )
        is not None
    )


async def _condition(
    session: AsyncSession, environment: EnvironmentRecord, now: datetime
) -> tuple[Literal["active", "idle", "waiting_approval"], datetime]:
    if await has_active_use(session, environment.id):
        return "active", now
    waiting = tuple(
        await session.scalars(
            select(RunRecord)
            .join(ThreadRecord, ThreadRecord.current_run_id == RunRecord.id)
            .where(
                RunRecord.environment_id == environment.id,
                RunRecord.status == "waiting",
                RunRecord.wait_reason.in_(("approval", "multiple")),
                RunRecord.environment_use_started_at.is_not(None),
            )
        )
    )
    resources = (run.to_resource() for run in waiting)
    approvals = tuple(
        run
        for run in resources
        if run.pending is not None and any(call.kind == "approval" for call in run.pending.calls)
    )
    if approvals:
        return "waiting_approval", min(assume_utc(run.sealed_at) for run in approvals if run.sealed_at is not None)
    latest = await session.scalar(
        select(RunRecord.sealed_at)
        .where(
            RunRecord.environment_id == environment.id,
            RunRecord.environment_use_started_at.is_not(None),
            RunRecord.sealed_at.is_not(None),
        )
        .order_by(RunRecord.sealed_at.desc())
        .limit(1)
    )
    return "idle", assume_utc(latest) if latest else now
