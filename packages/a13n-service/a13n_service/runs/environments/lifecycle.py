"""The fenced lifecycle of managed environments: at most one outstanding external operation per instance.

An operation begins under the environment row lock: the status names it, `generation` advances and a durable
`operation_id` exists before any I/O. A dispatcher claims it with a token and expiry, calls the provider with
no database session held, and publishes only if generation, operation and claim still match, so a late result
never overwrites a newer operation. An expired claim lets the next dispatcher continue the *same* operation:
Harness lifecycle calls reconcile the instance they are bound to (preparation looks the instance up before
creating one; stop and destroy observe its actual state), so continuing never issues conflicting work. Errors
stay on the phase with the same operation ID, and maintenance revisits it at its fixed interval.

Reaching `ready` schedules the first renewal of a sandbox whose type expires it unless renewed (`renewal`), and
beginning any operation ends renewal.
"""

import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import anyio
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderOutcomeCertainty,
)
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import EnvironmentError as OperationError
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_logging import exception_details, get_logger
from sqlalchemy import ColumnElement, SQLColumnExpression, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import advisory_lock, lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.environment_templates.service import read_template, resolve_template
from a13n_service.resources.providers.service import ResolvedProvider, read_provider, resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import Target, close, construct, credential_version, provider_identity
from a13n_service.runs.environments.schemas import Certainty, EnvironmentFailure, Handle
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow
from a13n_service.settings import PUBLISH_SECONDS
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

logger = get_logger(__name__)

type Phase = Literal["creating", "starting", "stopping", "deleting"]
_PHASES: dict[str, Phase] = {
    "creating": "creating",
    "starting": "starting",
    "stopping": "stopping",
    "deleting": "deleting",
}
PHASES = frozenset(_PHASES)
_REACHES: dict[str, str] = {"creating": "ready", "starting": "ready", "stopping": "stopped", "deleting": "deleted"}
# Refusals that repeating the same call cannot overcome; they refuse new mounts until the cause is fixed.
PERMANENT = frozenset(
    {
        EnvironmentProviderErrorCategory.INVALID,
        EnvironmentProviderErrorCategory.UNSUPPORTED,
        EnvironmentProviderErrorCategory.MISSING,
        EnvironmentProviderErrorCategory.DENIED,
        EnvironmentProviderErrorCategory.CONFLICT,
    }
)
_CERTAINTY: dict[EnvironmentProviderOutcomeCertainty, Certainty] = {
    EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED: "not_dispatched",
    EnvironmentProviderOutcomeCertainty.KNOWN: "known",
    EnvironmentProviderOutcomeCertainty.UNKNOWN: "unknown",
}


def in_use(environment_id: SQLColumnExpression[str] | str) -> ColumnElement[bool]:
    """Whether an accepted or running run's frozen mounts name the instance: the durable active-use evidence."""
    return exists().where(
        # Literal, so the partial GIN index on active runs' mounts applies.
        text("runs.status IN ('accepted', 'running')"),
        RunRow.environment_mounts.contains(
            func.jsonb_build_array(func.jsonb_build_object("environment_id", environment_id))
        ),
    )


def mounted(environment_id: SQLColumnExpression[str] | str) -> ColumnElement[bool]:
    """Whether some thread's desired mounts name the instance."""
    return exists().where(ThreadEnvironmentRow.environment_id == environment_id)


async def begin(session: AsyncSession, environment: EnvironmentRow, phase: Phase) -> None:
    """Start a new operation. The caller holds the row lock and has proved the previous one can no longer act."""
    environment.status = phase
    environment.generation += 1
    environment.operation_id = new_object_id("envoper")
    environment.operation_started_at = await now(session)
    environment.operation_deadline = None
    environment.lease_owner = environment.lease_token_hash = environment.lease_expires_at = None
    environment.failure = environment.renew_at = environment.expires_at = None


def supports(definition: EnvironmentProviderDefinition, phase: Literal["stopping", "deleting"]) -> bool:
    """Whether the type can take its instances through `phase`; an instance begun in any other would stick in it."""
    return definition.supports_stop if phase == "stopping" else definition.supports_destroy


def unusable(environment: EnvironmentRow) -> bool:
    """Whether a permanent failure refuses every new use of the instance until its cause is fixed or it is
    deleted."""
    return environment.failure is not None and environment.failure["permanent"]


def require_usable_state(environment: EnvironmentRow) -> None:
    """Refuse a new use, or a stop that would clear the failure, while a permanent failure makes the instance
    unusable; the refusal names the failure."""
    if unusable(environment):
        assert environment.failure is not None
        raise conflict("environment", environment.id, environment.failure["code"])


def settled(environment: EnvironmentRow) -> bool:
    """Whether the outstanding operation provably can no longer take effect, so a different one may replace it:
    nobody holds or silently lost its claim, and its last call did not end with an unknown outcome."""
    failure = environment.failure
    return environment.lease_owner is None and (failure is None or failure["certainty"] != "unknown")


async def lock_reservations(session: AsyncSession, workspace_id: str) -> None:
    """The workspace's reservation lock. It serializes reservations, so concurrent ones cannot pass the managed
    count together. Only reservations take it, and no transaction that locks an instance exclusively reserves, so
    new use, which share-locks instances, may reserve before or after locking them without a cycle."""
    await advisory_lock(session, "environments", workspace_id)


async def reserve(
    session: AsyncSession,
    principal: Principal,
    scope: WorkspaceScope,
    template_id: str,
    *,
    limit: int,
    name: str | None = None,
) -> EnvironmentRow:
    """A new workspace-managed instance in `creating`, from an enabled template of an enabled provider the principal
    may run, while the workspace holds fewer than `limit` managed instances that are not deleted. Nothing external
    exists until its create operation is dispatched, which reads the template then."""
    template = await resolve_template(session, principal, scope, template_id)
    await resolve_provider(session, principal, EnvironmentProviderRow, scope, template.provider_id)
    await lock_reservations(session, scope.workspace_id)
    held = await session.scalar(
        select(func.count())
        .select_from(EnvironmentRow)
        .where(
            EnvironmentRow.workspace_id == scope.workspace_id,
            EnvironmentRow.template_id.is_not(None),
            EnvironmentRow.status != "deleted",
        )
    )
    if (held or 0) >= limit:
        raise conflict("workspace", scope.workspace_id, "environment_limit", limit=limit)
    environment = EnvironmentRow(
        id=new_object_id("env"),
        organization_id=template.organization_id,
        workspace_id=template.workspace_id,
        provider_id=template.provider_id,
        template_id=template.id,
        name=name or template.name,
        generation=0,
        created_by_id=principal.id,
    )
    await begin(session, environment, "creating")
    session.add(environment)
    await session.flush()
    return environment


@dataclass(frozen=True, slots=True)
class Fault:
    """A refused or failed call, before publication binds it to its operation."""

    code: str
    message: str
    certainty: Certainty
    permanent: bool = False


@dataclass(frozen=True, slots=True)
class Operation:
    """One claimed dispatch of an instance's outstanding operation."""

    environment_id: str
    phase: Phase
    generation: int
    operation_id: str
    token: str = field(repr=False)
    target: Target
    # An earlier dispatch may have taken effect, so failing before this one dispatches settles nothing.
    unresolved: bool
    seconds: float
    # The provider credential's version now, and whether it changed since that credential last reached the instance.
    credential_version: str | None
    credential_changed: bool


@dataclass(frozen=True, slots=True)
class Outcome:
    state: EnvironmentState | None
    fault: Fault | None = None


# The provider no longer has the instance: nothing can bring it back, and it is never silently recreated.
LOST = Fault(
    "environment_lost",
    "The sandbox no longer exists at its provider; delete this environment and use a new one",
    "known",
    permanent=True,
)
# The provider no longer shows the instance, but a credential written since it last did may belong to another account.
UNSEEN = Fault(
    "provider_credential_changed",
    "The provider's credential changed since it last reached this sandbox, and the new one does not see it",
    "known",
)


def lost(credential_changed: bool) -> Fault:
    """What a provider that no longer shows the instance proves: that it is lost, unless its credential changed."""
    return UNSEEN if credential_changed else LOST


def reached_with(handle: Handle, provider: ResolvedProvider) -> tuple[str | None, bool]:
    """The provider credential's version, and whether it changed since a credential last reached the instance."""
    version = credential_version(provider)
    return version, handle.credential_version not in {None, version}


def record_failure(environment: EnvironmentRow, fault: Fault, at: datetime, *, unresolved: bool) -> None:
    certainty: Certainty = "unknown" if unresolved and fault.certainty == "not_dispatched" else fault.certainty
    environment.failure = EnvironmentFailure(
        code=fault.code,
        message=fault.message,
        certainty=certainty,
        # An unknown outcome is never final: the same operation can still confirm it.
        permanent=fault.permanent and certainty != "unknown",
        operation_id=environment.operation_id,
        at=at,
    ).model_dump(mode="json")


async def _refusal(
    session: AsyncSession, runtime: Runtime, environment: EnvironmentRow, provider: ResolvedProvider
) -> Fault | None:
    """Why this dispatch may not proceed, if it may not; the first one freezes what the instance is built from."""
    assert environment.template_id is not None
    try:
        identity = provider_identity(runtime.registry, provider)
    except ServiceError as error:
        return Fault("environment_provider_unavailable", error.message, "not_dispatched", permanent=True)
    if environment.status in {"creating", "starting"} and not provider.enabled:
        return Fault("environment_provider_disabled", "The environment provider is disabled", "not_dispatched", True)
    if environment.handle is None:
        template = await read_template(session, environment.template_id)
        if not template.enabled:
            return Fault("environment_template_disabled", "The template is disabled", "not_dispatched", True)
        if template.provider_id != environment.provider_id:
            # The recipe is the new provider's; the reservation's provider cannot build it.
            message = "The template moved to another provider after this reservation; reserve a new environment"
            return Fault("environment_template_moved", message, "not_dispatched", permanent=True)
        definition = runtime.registry.get("environment", provider.type)
        # Freeze effective defaults too: rebuilding an adapter after an upgrade must use the same recipe.
        try:
            recipe = definition.validate_environment(template.config.recipe).model_dump(mode="json")
        except EnvironmentProviderError as error:
            return fault_of(error, dispatched=False)
        environment.handle = Handle(recipe=recipe).model_dump(mode="json")
        environment.provider_identity = identity
    elif environment.provider_identity != identity:
        message = "The provider now points at another account or endpoint; restore it or delete this environment"
        return Fault("provider_identity_changed", message, "not_dispatched", permanent=True)
    return None


async def claim(runtime: Runtime, environment_id: str, *, owner: str) -> Operation | None:
    """Claim the outstanding operation unless nobody needs to or someone else holds it."""
    seconds = runtime.settings.environments.operation_seconds
    async with transaction(runtime.storage) as session:
        environment = await lock(session, EnvironmentRow, environment_id)
        phase = None if environment is None else _PHASES.get(environment.status)
        if environment is None or phase is None or environment.operation_id is None:
            return None
        current = await now(session)
        if environment.lease_expires_at is not None and environment.lease_expires_at > current:
            return None
        assert environment.provider_id is not None, "only managed instances have operations"
        failure = environment.failure or {}
        unresolved = environment.lease_owner is not None or failure.get("certainty") == "unknown"
        # Maintenance acts for no principal and must still stop and destroy instances of a disabled provider.
        provider = await read_provider(session, EnvironmentProviderRow, environment.provider_id)
        if (fault := await _refusal(session, runtime, environment, provider)) is not None:
            environment.lease_owner = environment.lease_token_hash = environment.lease_expires_at = None
            record_failure(environment, fault, current, unresolved=unresolved)
            return None
        token = secrets.token_urlsafe(32)
        environment.lease_owner, environment.lease_token_hash = owner, secret_hash(token)
        deadline = current + timedelta(seconds=seconds)
        environment.operation_deadline = deadline
        environment.lease_expires_at = deadline + timedelta(seconds=PUBLISH_SECONDS)
        handle = Handle.model_validate(environment.handle)
        version, changed = reached_with(handle, provider)
        return Operation(
            environment_id=environment.id,
            phase=phase,
            generation=environment.generation,
            operation_id=environment.operation_id,
            token=token,
            target=Target(environment.id, provider, handle.recipe, handle.state),
            unresolved=unresolved,
            seconds=seconds,
            credential_version=version,
            credential_changed=changed,
        )


class _InstanceLost(Exception):
    """Reconciliation proved the instance no longer exists."""


async def _call(adapter: Environment, phase: Phase) -> EnvironmentState | None:
    match phase:
        case "creating":
            await adapter.prepare()
        case "starting":
            # Resume only the instance that exists; a lost one is never silently recreated.
            if await adapter.reconcile() == "absent":
                raise _InstanceLost()
            await adapter.prepare()
        case "stopping":
            await adapter.stop()
        case "deleting":
            await adapter.destroy()
            return None
    return adapter.dump_state()


def fault_of(error: Exception, *, dispatched: bool) -> Fault:
    if isinstance(error, EnvironmentProviderError):
        certainty = _CERTAINTY[error.certainty]
        permanent = certainty != "unknown" and error.category in PERMANENT
        return Fault(error.code, error.safe_projection().message, certainty, permanent)
    if isinstance(error, ServiceError):
        return Fault("environment_provider_unavailable", error.message, "not_dispatched", permanent=True)
    certainty: Certainty = "unknown" if dispatched else "not_dispatched"
    if isinstance(error, TimeoutError):
        return Fault("environment_operation_timeout", "The provider did not answer before the deadline", certainty)
    code = error.code if isinstance(error, OperationError) else "environment_operation_failed"
    return Fault(code, "The provider operation failed", certainty)


async def perform(runtime: Runtime, operation: Operation) -> Outcome:
    """One bounded provider call for the claimed phase, with no database session held."""
    adapter: Environment | None = None
    dispatched = False
    try:
        with anyio.fail_after(operation.seconds):
            # Lifecycle calls act as the instance's owner, so an adapter finds it by the environment ID even before
            # its state was recorded; only preparation ever creates one, and stop and destroy never prepare.
            adapter = await construct(runtime, operation.target, operation_id=operation.operation_id, allow_create=True)
            dispatched = True
            return Outcome(await _call(adapter, operation.phase))
    except _InstanceLost:
        return Outcome(adapter.dump_state() if adapter is not None else None, lost(operation.credential_changed))
    except Exception as error:
        logger.warning(
            "Environment operation failed",
            extra={
                "environment_id": operation.environment_id,
                "operation_id": operation.operation_id,
                "phase": operation.phase,
                "exception_details": exception_details(error),
            },
        )
        # A known target stays recorded even when a later step failed, so the next dispatcher can recover it.
        return Outcome(adapter.dump_state() if adapter is not None else None, fault_of(error, dispatched=dispatched))
    finally:
        if adapter is not None:
            await close(adapter)


async def publish(runtime: Runtime, operation: Operation, outcome: Outcome) -> None:
    """Record the call's outcome if the claim is still current; a superseded dispatcher changes nothing."""
    async with transaction(runtime.storage) as session:
        environment = await lock(session, EnvironmentRow, operation.environment_id)
        if (
            environment is None
            or environment.generation != operation.generation
            or environment.operation_id != operation.operation_id
            or environment.lease_token_hash is None
            or not hmac.compare_digest(environment.lease_token_hash, secret_hash(operation.token))
        ):
            return
        current = await now(session)
        environment.lease_owner = environment.lease_token_hash = environment.lease_expires_at = None
        environment.operation_deadline = None
        handle = Handle.model_validate(environment.handle)
        if outcome.fault is not None:
            if outcome.state is not None:
                environment.handle = handle.model_copy(update={"state": outcome.state}).model_dump(mode="json")
            record_failure(environment, outcome.fault, current, unresolved=operation.unresolved)
            return
        reached = _REACHES[operation.phase]
        environment.status = reached
        environment.operation_id = environment.operation_started_at = None
        environment.failure = None
        if reached == "deleted":
            environment.handle = None
        else:
            update = {"state": outcome.state, "credential_version": operation.credential_version}
            environment.handle = handle.model_copy(update=update).model_dump(mode="json")
        if reached == "ready":
            environment.last_used_at = current
            assert environment.provider_identity is not None, "the first claim froze it"
            if runtime.registry.get("environment", environment.provider_identity["type"]).requires_keepalive:
                environment.renew_at = current
        record(
            session,
            WorkspaceScope(environment.organization_id, environment.workspace_id),
            actor_id=None,
            action=f"environment.{reached}",
            target_kind="environment",
            target_id=environment.id,
            details={"operation_id": operation.operation_id},
        )


async def advance(runtime: Runtime, environment_id: str, *, owner: str) -> None:
    """Dispatch the instance's outstanding operation once, unless nobody needs to or someone else is."""
    operation = await claim(runtime, environment_id, owner=owner)
    if operation is None:
        return
    try:
        outcome = await perform(runtime, operation)
    except BaseException:
        # Interrupted mid-call: the effect is unknown until the next dispatcher reconciles the same operation.
        interrupted = Fault("environment_operation_interrupted", "The dispatcher stopped mid-call", "unknown")
        with anyio.CancelScope(shield=True), anyio.move_on_after(PUBLISH_SECONDS):
            await publish(runtime, operation, Outcome(None, interrupted))
        raise
    await publish(runtime, operation, outcome)
