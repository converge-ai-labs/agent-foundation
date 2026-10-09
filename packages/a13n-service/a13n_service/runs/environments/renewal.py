"""Renewal: keeping a ready sandbox alive when its type ends sandboxes that are not renewed.

Reaching `ready` makes a renewal of such a sandbox (`requires_keepalive`) due at once. Each renewal asks the provider,
within `environments.renewal_seconds`, to keep the sandbox for at least the adapter's keepalive horizon, records the
expiry it reports and falls due again halfway to it. The settings keep a renewal interval and two calls inside half
the renewal horizon, so a due renewal finishes before the expiry. A template's idle policy still stops and deletes
these sandboxes, and beginning any operation ends renewal, so a stopping, stopped or deleted instance is never renewed.

A renewal only extends a deadline; it never creates, starts or stops anything, so the lightest claim is enough.
Claiming pushes `renew_at` past the call's deadline and publication. The outcome is recorded only while `renew_at`
still holds that claim: beginning an operation clears it, and a later claim replaces it. An interrupted renewal
records nothing; its claim expires and a later pass renews. A provider whose identity changed or cannot be resolved
is not called until it is restored, as for runs.

What the provider answers:
- the expiry: recorded, the failure cleared, and the credential version that reached the sandbox remembered;
- no such sandbox: permanently `environment_lost`, unless the provider's credential changed since it last reached
  the sandbox; that failure is not permanent, since the new credential may belong to another account;
- the sandbox stopped: the instance is `stopped`, and the next run starts it;
- that it cannot keep the sandbox that long, near a vendor's hard lifetime: expected, so nothing is recorded, and
  the next renewal waits for the reported expiry, when the sandbox is found gone or stopped;
- any other failure: recorded, permanent by its category as for operations, and retried halfway to the known expiry
  while one remains and the failure is transient, else after as long as the same failure has lasted, at most ten
  minutes.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
from a13n_environment.errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderOutcomeCertainty,
    provider_error,
)
from a13n_logging import exception_details, get_logger

from a13n_service.infra.audit import record
from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import Target, close, open_provider, provider_identity
from a13n_service.runs.environments.lifecycle import LOST, Fault, fault_of, lost, reached_with, record_failure
from a13n_service.runs.environments.schemas import EnvironmentFailure, Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.settings import PUBLISH_SECONDS, RENEWAL_HORIZON_SECONDS
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)

_MAX_BACKOFF = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class Renewal:
    """One claimed renewal; its claim is the `renew_at` it set."""

    environment_id: str
    claim: datetime
    target: Target
    seconds: float
    credential_version: str | None
    credential_changed: bool


async def renew(runtime: Runtime, environment_id: str) -> None:
    """Renew the sandbox once, if its renewal is due and nobody else is renewing it."""
    renewal = await claim(runtime, environment_id)
    if renewal is not None:
        await publish(runtime, renewal, await perform(runtime, renewal))


async def claim(runtime: Runtime, environment_id: str) -> Renewal | None:
    """Claim the sandbox's renewal if it is due; nobody else holds it while `renew_at` is in the future."""
    seconds = runtime.settings.environments.renewal_seconds
    async with transaction(runtime.storage) as session:
        environment = await lock(session, EnvironmentRow, environment_id)
        current = await now(session)
        if environment is None or environment.renew_at is None or environment.renew_at > current:
            return None
        environment.renew_at = claimed = current + timedelta(seconds=seconds + PUBLISH_SECONDS)
        assert environment.provider_id is not None, "only managed sandboxes are renewed"
        # Maintenance acts for no principal, and renews a disabled provider's sandboxes until they stop.
        provider = await read_provider(session, EnvironmentProviderRow, environment.provider_id)
        try:
            if provider_identity(runtime.registry, provider) != environment.provider_identity:
                return None
        except ServiceError:
            return None
        handle = Handle.model_validate(environment.handle)
        version, changed = reached_with(handle, provider)
        target = Target(environment.id, provider, handle.recipe, handle.state)
        return Renewal(environment.id, claimed, target, seconds, version, changed)


async def perform(runtime: Runtime, renewal: Renewal) -> datetime | Exception:
    """One bounded keepalive with no database session held: the expiry the provider reports, or why not."""
    adapter = None
    try:
        with anyio.fail_after(renewal.seconds):
            adapter = await open_provider(runtime, renewal.target)
            target = renewal.target
            deadline = datetime.now(UTC) + adapter.keepalive_horizon(
                dict(target.recipe), environment_id=target.environment_id, state=target.state
            )
            expiry = await adapter.keepalive(
                dict(target.recipe),
                environment_id=target.environment_id,
                state=target.state,
                deadline=deadline,
                operation_id=new_object_id("envrenew"),
            )
            if expiry is None or expiry < deadline:
                raise provider_error(
                    target.state.provider_key if target.state is not None else "environment",
                    "provider_keepalive_unsatisfied",
                    EnvironmentProviderErrorCategory.UNAVAILABLE,
                    certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
                )
            return expiry
    except Exception as error:
        return error
    finally:
        if adapter is not None:
            await close(adapter)


async def publish(runtime: Runtime, renewal: Renewal, outcome: datetime | Exception) -> None:
    """Record the outcome if the claim still holds; a superseded renewal changes nothing."""
    async with transaction(runtime.storage) as session:
        environment = await lock(session, EnvironmentRow, renewal.environment_id)
        if environment is None or environment.renew_at != renewal.claim:
            return
        current = await now(session)
        if isinstance(outcome, datetime):
            environment.expires_at = outcome
            environment.renew_at = current + max(outcome - current, timedelta()) / 2
            environment.failure = None
            handle = Handle.model_validate(environment.handle)
            if handle.credential_version != renewal.credential_version:
                update = {"credential_version": renewal.credential_version}
                environment.handle = handle.model_copy(update=update).model_dump(mode="json")
            return
        category = outcome.category if isinstance(outcome, EnvironmentProviderError) else None
        code = outcome.code if isinstance(outcome, EnvironmentProviderError) else None
        if code == "provider_keepalive_limit":
            horizon = current + timedelta(seconds=RENEWAL_HORIZON_SECONDS)
            environment.renew_at = max(current, environment.expires_at or horizon)
        elif code == "provider_target_stopped":
            environment.status = "stopped"
            environment.renew_at = environment.expires_at = environment.failure = None
            record(
                session,
                WorkspaceScope(environment.organization_id, environment.workspace_id),
                actor_id=None,
                action="environment.stopped",
                target_kind="environment",
                target_id=environment.id,
                details={"reason": "provider"},
            )
        elif category == EnvironmentProviderErrorCategory.MISSING:
            _fail(environment, lost(renewal.credential_changed), current)
        else:
            logger.warning(
                "Environment renewal failed",
                extra={"environment_id": renewal.environment_id, "exception_details": exception_details(outcome)},
            )
            _fail(environment, fault_of(outcome, dispatched=True), current)


def _fail(environment: EnvironmentRow, fault: Fault, current: datetime) -> None:
    """Record the failure, keeping the first record of a repeated one, and schedule the retry; a loss ends renewal."""
    if environment.failure is None or environment.failure["code"] != fault.code:
        record_failure(environment, fault, current, unresolved=False)
    since = EnvironmentFailure.model_validate(environment.failure).at
    if fault is LOST:
        environment.renew_at = environment.expires_at = None
    elif not fault.permanent and environment.expires_at is not None and environment.expires_at > current:
        environment.renew_at = current + (environment.expires_at - current) / 2
    else:
        environment.renew_at = current + min(current - since, _MAX_BACKOFF)
