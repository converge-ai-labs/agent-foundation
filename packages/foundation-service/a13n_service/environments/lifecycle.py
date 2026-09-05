"""Fenced Environment lifecycle operations with no database scope across Provider I/O."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import EnvironmentStatus, JsonObject, TemplateConfiguration, retention_action
from .errors import is_target_identity_conflict
from .identity import target_identity as scoped_target_identity
from .models import (
    EnvironmentCommandRecord,
    EnvironmentProviderRecord,
    EnvironmentRecord,
    EnvironmentTemplateRevisionRecord,
)
from .retention import has_active_use, refresh_retention

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext

Action = Literal["prepare", "reconcile", "stop", "delete", "keepalive"]


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
        from a13n_service.interactions.attempts import lock_attempt_lease

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
            if run is not None:
                run.environment_use_started_at = run.environment_use_started_at or now
                if row.retention_condition != "active":
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
                condition = await refresh_retention(session, row, now)
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
        from a13n_service.interactions.attempts import lock_attempt_lease

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

    async def authorize_command(self, operation: LifecycleOperation) -> None:
        async with transaction(self.sessions) as session:
            row = await session.get(EnvironmentRecord, operation.environment_id)
            if row is None:
                raise ValueError("Environment is unavailable")
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
            schema_version=operation.recipe.configuration_schema_version, value=operation.recipe.configuration
        )
        runtime = await provider.create_runtime(
            configuration=provider.provider_configuration_model.model_validate(operation.provider_configuration),
            credential=credential,
            context=ProviderRuntimeContext(
                operation.environment_id, operation.operation_id, self.storage_root, operation.managed
            ),
        )
        return provider.create_environment(
            configuration=configuration, environment_id=operation.environment_id, state=operation.state, runtime=runtime
        )

    async def execute(self, operation: LifecycleOperation) -> LifecycleResult:
        environment = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                if operation.action in {"stop", "delete"}:
                    await self.authorize_command(operation)
                environment = await self.construct(operation)
                observation = None
                if operation.action == "reconcile":
                    observation = await environment.reconcile()
                elif operation.action == "prepare":
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
                generation = await self.publish(operation, environment, observation=observation)
            return LifecycleResult(environment, generation)
        except BaseException as error:
            if is_target_identity_conflict(error):
                error = EnvironmentError(
                    "This backend target already has an Environment owner.", code="environment_target_conflict"
                )
            # A known target must survive readiness failure or cancellation. Unknown
            # operations retain their identity for reconciliation, never a new create.
            task = asyncio.create_task(self.publish(operation, environment, error=error))
            try:
                await asyncio.shield(task)
            except BaseException as publication_error:
                error.add_note(f"Lifecycle failure publication also failed: {publication_error!r}")
            finally:
                if environment is not None:
                    try:
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
        observation: Literal["running", "stopped", "absent"] | None = None,
    ) -> int:
        succeeded = error is None
        # Construction cannot dispatch target work. Provider errors explicitly distinguish
        # a known failure from an operation whose external effect remains unknown.
        resolved = (
            succeeded
            or environment is None
            or isinstance(error, EnvironmentError)
            or (
                isinstance(error, EnvironmentProviderError)
                and error.certainty != EnvironmentProviderOutcomeCertainty.UNKNOWN
            )
        )
        state = environment.dump_state() if environment is not None else operation.state
        provider = self.catalog.require(operation.provider_type)
        identity = None
        target_conflict = isinstance(error, EnvironmentError) and error.code == "environment_target_conflict"
        if not target_conflict and (state is not None or succeeded) and operation.action in {"prepare", "reconcile"}:
            configuration = provider.validate_configuration(
                schema_version=operation.recipe.configuration_schema_version, value=operation.recipe.configuration
            )
            identity = scoped_target_identity(
                operation.provider_type,
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
            if succeeded:
                if operation.action == "prepare" and row.generation == 0:
                    row.generation = 1
                if operation.action == "delete":
                    row.status, row.state, row.target_identity = "deleted", None, None
                elif operation.action == "stop":
                    row.status = "stopped"
                elif operation.action == "prepare":
                    row.status = "running"
                elif operation.action == "reconcile":
                    assert observation is not None
                    row.status = "unavailable" if observation == "absent" else observation
                row.last_error = None
            else:
                if operation.action in {"prepare", "reconcile"}:
                    row.status = "unavailable"
                row.last_error = {
                    "code": "environment_operation_failed" if resolved else "environment_operation_unresolved"
                }
            if resolved:
                command = await session.get(EnvironmentCommandRecord, operation.operation_id)
                if command is not None:
                    command.status = "completed" if succeeded else "failed"
                    command.completed_at = now
                row.operation_id = row.operation_action = row.operation_owner = None
                row.operation_expires_at = None
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
            revision = await session.get(EnvironmentTemplateRevisionRecord, row.template_revision_id)
            if revision is None:
                return
            recipe = revision.to_resource()
            condition = await refresh_retention(session, row, now)
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
                elif row.operation_action == "prepare":
                    action = "reconcile"
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
            result = await self.execute(operation)
            await result.environment.close()
