"""The fenced lifecycle of managed environments: at most one outstanding external operation per instance.

An operation begins under the environment row lock: the status names it, `generation` advances and a durable
`operation_id` exists before any I/O. A dispatcher claims it with a token and expiry, calls the provider with
no database session held, and publishes only if generation, operation and claim still match, so a late result
never overwrites a newer operation. An expired claim lets the next dispatcher continue the *same* operation:
Provider management calls reconcile the explicit target (create looks up its correlation identity before
allocation; start, stop and destroy act on saved state), so continuing never issues conflicting work. Errors
stay on the phase with the same operation ID, and maintenance revisits it at its fixed interval.

Reaching `ready` schedules the first renewal of a sandbox whose type expires it unless renewed (`renewal`), and
beginning any operation ends renewal.
"""

import hmac
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

import anyio
from a13n_harness.environment.errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderOutcomeCertainty,
)
from a13n_harness.environment.models import EnvironmentError as OperationError
from a13n_harness.environment.models import EnvironmentState
from a13n_logging import exception_details, get_logger
from pydantic import ValidationError
from sqlalchemy import ColumnElement, SQLColumnExpression, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import advisory_lock, lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.environment.definition import EnvironmentManagementDefinition
from a13n_service.providers.environment.errors import observed_environment_state
from a13n_service.providers.environment.management import EnvironmentProvider
from a13n_service.providers.environment.metrics import MetricsLaunch
from a13n_service.resources.environment_templates.service import read_template, resolve_template
from a13n_service.resources.providers.service import (
    ResolvedProvider,
    read_provider,
    rejection_reason,
    resolve_provider,
)
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import Target, close, credential_version, open_provider, provider_identity
from a13n_service.runs.environments.monitoring import launch
from a13n_service.runs.environments.schemas import Certainty, EnvironmentFailure, Handle, MetricsApplication
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ToolExecutionRow
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
    """Whether the instance is in active use: an accepted or running run's frozen mounts name it, or a desktop
    context holds the reservation of its native target under any alias."""
    from a13n_service.runs.desktop_assistance.tables import DesktopAssistanceRow, DesktopTargetRow

    held = aliased(EnvironmentRow)
    candidate = aliased(EnvironmentRow)
    from a13n_service.runs.desktop_assistance.admission import TARGET_FIELDS

    same_target = (
        held.handle["state"]["provider_key"].astext == candidate.handle["state"]["provider_key"].astext
    ) & or_(
        *(
            (held.handle["state"]["provider_key"].astext == provider)
            & (held.handle["state"]["state"][field].astext == candidate.handle["state"]["state"][field].astext)
            for provider, field in TARGET_FIELDS.items()
        )
    )
    held_target = exists(
        select(DesktopAssistanceRow.id)
        .join(DesktopTargetRow, DesktopTargetRow.holder_id == DesktopAssistanceRow.id)
        .join(held, held.id == DesktopAssistanceRow.environment_id)
        .join(candidate, same_target)
        .where(candidate.id == environment_id)
        .correlate_except(DesktopAssistanceRow, DesktopTargetRow, held, candidate)
    )
    return (
        exists().where(
            text("runs.status IN ('accepted', 'running')"),
            RunRow.environment_mounts.contains(
                func.jsonb_build_array(func.jsonb_build_object("environment_id", environment_id))
            ),
        )
        | exists().where(
            ToolExecutionRow.completed_at.is_(None),
            ToolExecutionRow.environment_mounts.contains(
                func.jsonb_build_array(func.jsonb_build_object("environment_id", environment_id))
            ),
        )
        | held_target
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


def supports(definition: EnvironmentManagementDefinition, phase: Literal["stopping", "deleting"]) -> bool:
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
    """A new workspace-managed instance in `reserved`, from an enabled template of an enabled provider the principal
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
        status="reserved",
        created_by_id=principal.id,
    )
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
    metrics: MetricsLaunch | None = field(default=None, repr=False)
    e2b_desktop_template: str | None = None
    modal_desktop_image: str | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    state: EnvironmentState | None
    fault: Fault | None = None
    pending: bool = False


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
            recipe = definition.validate_environment(template.config.recipe)
            if environment.status == "creating":
                await launch.freeze(
                    session,
                    runtime.keys,
                    environment,
                    supported=definition.metrics_launch is not None and definition.metrics_launch(recipe),
                )
        except (EnvironmentProviderError, ServiceError) as error:
            return fault_of(error, dispatched=False)
        preparation = None
        if provider.type == "e2b" and getattr(recipe, "template", None) == "auto":
            from a13n_service.runs.environments.e2b_templates import bind

            try:
                preparation = await bind(runtime, session, environment, provider)
            except (EnvironmentProviderError, ServiceError) as error:
                return fault_of(error, dispatched=False)
        modal_preparation = None
        if provider.type == "modal" and getattr(recipe, "image", None) == "auto":
            from a13n_service.runs.environments.modal_images import bind

            try:
                modal_preparation = await bind(runtime, session, environment, provider)
            except (EnvironmentProviderError, ServiceError) as error:
                return fault_of(error, dispatched=False)
        environment.handle = Handle(
            recipe=recipe.model_dump(mode="json"),
            e2b_desktop_template=preparation,
            modal_desktop_image=modal_preparation,
        ).model_dump(mode="json")
        environment.provider_identity = identity
    elif environment.provider_identity != identity:
        message = "The provider now points at another account or endpoint; restore it or delete this environment"
        return Fault("provider_identity_changed", message, "not_dispatched", permanent=True)
    return None


async def claim(runtime: Runtime, environment_id: str, *, owner: str) -> Operation | None:
    """Claim the outstanding operation unless nobody needs to or someone else holds it."""
    async with transaction(runtime.storage) as session:
        environment = await lock(session, EnvironmentRow, environment_id)
        return await claim_locked(runtime, session, environment, owner=owner)


async def claim_locked(
    runtime: Runtime,
    session: AsyncSession,
    environment: EnvironmentRow | None,
    *,
    owner: str,
    provider: ResolvedProvider | None = None,
) -> Operation | None:
    """Claim under the caller's row lock, allowing begin and first claim to commit together."""
    seconds = runtime.settings.environments.operation_seconds
    phase = None if environment is None else _PHASES.get(environment.status)
    if environment is None or phase is None or environment.operation_id is None:
        return None
    current = await now(session)
    if environment.lease_expires_at is not None and environment.lease_expires_at > current:
        return None
    assert environment.provider_id is not None, "only managed instances have operations"
    failure = environment.failure or {}
    unresolved = environment.lease_owner is not None or failure.get("certainty") == "unknown"
    if phase == "creating" and failure.get("permanent") and settled(environment) and environment.handle:
        handle = Handle.model_validate(environment.handle)
        if handle.recipe.get("template") == "auto" and handle.e2b_desktop_template:
            from a13n_service.runs.environments.e2b_templates import E2BDesktopTemplateRow

            build = await session.get(E2BDesktopTemplateRow, handle.e2b_desktop_template)
            if build is not None and build.phase == "failed":
                # Preserve the original actionable failure until explicit retry. Cleanup still advances.
                return None
        if handle.recipe.get("image") == "auto" and handle.modal_desktop_image:
            from a13n_service.runs.environments.modal_images import ModalDesktopImageRow

            build = await session.get(ModalDesktopImageRow, handle.modal_desktop_image)
            if build is not None and build.phase == "failed":
                return None
    # Maintenance acts for no principal and must still stop and destroy instances of a disabled provider.
    if provider is None:
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
        metrics=launch.restore(runtime.keys, environment) if phase == "creating" else None,
        e2b_desktop_template=handle.e2b_desktop_template,
        modal_desktop_image=handle.modal_desktop_image,
    )


async def _call(provider: EnvironmentProvider, operation: Operation) -> EnvironmentState | None:
    target = operation.target
    match operation.phase:
        case "creating":
            return await provider.create(
                dict(target.recipe),
                environment_id=target.environment_id,
                operation_id=operation.operation_id,
                state=target.state,
                **({"metrics": operation.metrics} if operation.metrics is not None else {}),
            )
        case "starting":
            return await provider.start(
                dict(target.recipe),
                environment_id=target.environment_id,
                operation_id=operation.operation_id,
                state=target.state,
            )
        case "stopping":
            return await provider.stop(
                dict(target.recipe),
                environment_id=target.environment_id,
                operation_id=operation.operation_id,
                state=target.state,
            )
        case "deleting":
            await provider.destroy(
                dict(target.recipe),
                environment_id=target.environment_id,
                operation_id=operation.operation_id,
                state=target.state,
            )
            return None


def public_message(error: EnvironmentProviderError) -> str:
    """The safe message with the Provider and its code and, when a schema refused the input, the fields and the
    schema's reasons, never the input or upstream text."""
    origin = ", ".join(part for part in (error.context.provider_key, error.code) if part)
    message = f"{error.safe_projection().message.removesuffix('.')} ({origin})"
    if isinstance(error.__cause__, ValidationError):
        return f"{message}: {rejection_reason(error.__cause__)}."
    return f"{message}."


def fault_of(error: Exception, *, dispatched: bool) -> Fault:
    if isinstance(error, EnvironmentProviderError):
        certainty = _CERTAINTY[error.certainty]
        permanent = certainty != "unknown" and error.category in PERMANENT
        return Fault(error.code, public_message(error), certainty, permanent)
    if isinstance(error, ServiceError):
        return Fault("environment_provider_unavailable", error.message, "not_dispatched", permanent=True)
    certainty: Certainty = "unknown" if dispatched else "not_dispatched"
    if isinstance(error, TimeoutError):
        return Fault("environment_operation_timeout", "The provider did not answer before the deadline", certainty)
    code = error.code if isinstance(error, OperationError) else "environment_operation_failed"
    return Fault(code, "The provider operation failed", certainty)


async def perform(runtime: Runtime, operation: Operation) -> Outcome:
    """One bounded provider call for the claimed phase, with no database session held."""
    provider: EnvironmentProvider | None = None
    dispatched = False
    try:
        with anyio.fail_after(operation.seconds):
            if operation.e2b_desktop_template is not None and operation.target.recipe.get("template") == "auto":
                if operation.phase == "deleting":
                    # Preparation never allocated a sandbox. Its private base remains a reusable account asset.
                    return Outcome(None)
                if operation.phase == "creating":
                    from a13n_service.runs.environments.e2b_templates import prepare

                    assert isinstance(operation.target.account, ResolvedProvider)
                    template = await prepare(runtime, operation.e2b_desktop_template, operation.target.account)
                    if template is None:
                        return Outcome(None, pending=True)
                    operation = await _pin_e2b_template(runtime, operation, template)
            if operation.modal_desktop_image is not None and operation.target.recipe.get("image") == "auto":
                if operation.phase == "deleting":
                    return Outcome(None)
                if operation.phase == "creating":
                    from a13n_service.runs.environments.modal_images import prepare

                    assert isinstance(operation.target.account, ResolvedProvider)
                    image = await prepare(runtime, operation.modal_desktop_image, operation.target.account)
                    if image is None:
                        return Outcome(None, pending=True)
                    await _pin_modal_image(runtime, operation, image)
                    # Persist first; the next claim gets a full operation deadline for sandbox startup.
                    return Outcome(None, pending=True)
            if operation.metrics is not None:
                endpoint = operation.metrics.environment.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
                if endpoint is not None:
                    await runtime.endpoint_policy.validate(endpoint)
            provider = await open_provider(runtime, operation.target)
            dispatched = True
            return Outcome(await _call(provider, operation))
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
        fault = (
            lost(operation.credential_changed)
            if operation.phase == "starting"
            and isinstance(error, EnvironmentProviderError)
            and error.category == EnvironmentProviderErrorCategory.MISSING
            else fault_of(error, dispatched=dispatched)
        )
        if (operation.e2b_desktop_template is not None and operation.target.recipe.get("template") == "auto") or (
            operation.modal_desktop_image is not None and operation.target.recipe.get("image") == "auto"
        ):
            # Build uncertainty is retained by its own ledger; no sandbox allocation was dispatched yet.
            fault = replace(fault, certainty="not_dispatched")
        return Outcome(observed_environment_state(error, operation.target.state), fault)
    finally:
        if provider is not None:
            await close(provider)


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
        if outcome.pending:
            environment.failure = None
            return
        if outcome.fault is not None:
            environment.handle = handle.model_copy(update={"state": outcome.state}).model_dump(mode="json")
            preparing = (handle.e2b_desktop_template is not None and handle.recipe.get("template") == "auto") or (
                handle.modal_desktop_image is not None and handle.recipe.get("image") == "auto"
            )
            fault = replace(outcome.fault, certainty="not_dispatched") if preparing else outcome.fault
            record_failure(environment, fault, current, unresolved=operation.unresolved and not preparing)
            return
        reached = _REACHES[operation.phase]
        environment.status = reached
        environment.operation_id = environment.operation_started_at = None
        environment.failure = None
        if operation.phase == "creating" and environment.metrics_application is not None:
            applied = MetricsApplication.model_validate(environment.metrics_application)
            environment.metrics_application = applied.model_copy(update={"applied_at": current}).model_dump(mode="json")
            environment.metrics_launch = None
        if reached == "deleted":
            environment.handle = None
            environment.metrics_launch = None
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


async def _pin_e2b_template(runtime: Runtime, operation: Operation, template: str) -> Operation:
    """Resolve the frozen preparation before allocation; later retries use this same native identity."""
    async with transaction(runtime.storage) as session:
        row = await lock(session, EnvironmentRow, operation.environment_id)
        if (
            row is None
            or row.generation != operation.generation
            or row.operation_id != operation.operation_id
            or row.lease_token_hash != secret_hash(operation.token)
            or row.status != "creating"
        ):
            raise conflict("environment", operation.environment_id, "operation_superseded")
        handle = Handle.model_validate(row.handle)
        if handle.state is not None or handle.recipe.get("template") not in {"auto", template}:
            raise conflict("environment", row.id, "desktop_template_changed")
        recipe = {**handle.recipe, "template": template}
        row.handle = handle.model_copy(update={"recipe": recipe}).model_dump(mode="json")
    return replace(operation, target=replace(operation.target, recipe=recipe))


async def _pin_modal_image(runtime: Runtime, operation: Operation, image: str) -> Operation:
    """Resolve the frozen preparation before allocation; later retries use this same native identity."""
    async with transaction(runtime.storage) as session:
        row = await lock(session, EnvironmentRow, operation.environment_id)
        if (
            row is None
            or row.generation != operation.generation
            or row.operation_id != operation.operation_id
            or row.lease_token_hash != secret_hash(operation.token)
            or row.status != "creating"
        ):
            raise conflict("environment", operation.environment_id, "operation_superseded")
        handle = Handle.model_validate(row.handle)
        if handle.state is not None or handle.recipe.get("image") not in {"auto", image}:
            raise conflict("environment", row.id, "desktop_image_changed")
        recipe = {**handle.recipe, "image": image}
        row.handle = handle.model_copy(update={"recipe": recipe}).model_dump(mode="json")
    return replace(operation, target=replace(operation.target, recipe=recipe))


async def advance(runtime: Runtime, environment_id: str, *, owner: str) -> None:
    """Dispatch the instance's outstanding operation once, unless nobody needs to or someone else is."""
    operation = await claim(runtime, environment_id, owner=owner)
    if operation is None:
        return
    await dispatch(runtime, operation)


async def dispatch(runtime: Runtime, operation: Operation) -> None:
    """Perform a claimed operation here and publish its fenced outcome without retaining a transaction."""
    try:
        outcome = await perform(runtime, operation)
    except BaseException as error:
        # Interrupted mid-call: the effect is unknown until the next dispatcher reconciles the same operation.
        interrupted = Fault("environment_operation_interrupted", "The dispatcher stopped mid-call", "unknown")
        with anyio.CancelScope(shield=True), anyio.move_on_after(PUBLISH_SECONDS):
            await publish(
                runtime, operation, Outcome(observed_environment_state(error, operation.target.state), interrupted)
            )
        raise
    await publish(runtime, operation, outcome)
