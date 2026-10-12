"""Two sweeps: `maintain_environments` applies the current idle policies and continues every unfinished operation;
`renew_environments` renews the ready sandboxes that end unless renewed.

Each maintenance pass begins stop and delete operations for managed instances idle past their template's thresholds,
then dispatches a bounded batch of outstanding operations whose claim is free or expired. It revisits failed
operations at its fixed interval with the same operation ID; it never invents a new one. Renewal has its own sweep,
so a slow lifecycle call never delays it.
"""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Literal

import anyio
from a13n_logging import exception_details, get_logger
from sqlalchemy import Select, func, or_, select, text

from a13n_service.infra.audit import record
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.sweeps import Sweep
from a13n_service.resources.environment_templates.service import idle_past
from a13n_service.runs.environments.lifecycle import advance, begin, in_use, mounted, supports
from a13n_service.runs.environments.renewal import renew
from a13n_service.runs.environments.tables import OPERATIONS, EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.settings import PUBLISH_SECONDS
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)


async def maintain_environments(runtime: Runtime, *, owner: str) -> None:
    """One maintenance pass; `owner` names this process in the claims it takes."""
    batch = runtime.settings.environments.batch
    await _retire_idle(runtime, "deleting", batch)
    await _retire_idle(runtime, "stopping", batch)
    due = select(EnvironmentRow.id).where(
        # Literal, so the partial index on unfinished operations applies.
        text(f"environments.status IN {OPERATIONS}"),
        or_(EnvironmentRow.lease_expires_at.is_(None), EnvironmentRow.lease_expires_at < func.now()),
    )
    await _each(runtime, due.order_by(EnvironmentRow.updated_at), partial(advance, runtime, owner=owner))


async def renew_environments(runtime: Runtime) -> None:
    """One renewal pass: the due renewals, most urgent first."""
    due = select(EnvironmentRow.id).where(EnvironmentRow.renew_at <= func.now()).order_by(EnvironmentRow.renew_at)
    await _each(runtime, due, partial(renew, runtime))


async def _retire_idle(runtime: Runtime, phase: Literal["stopping", "deleting"], batch: int) -> None:
    """Begin idle stops or deletes; active use and mounts are rechecked with fresh statements under each lock."""
    since = func.coalesce(EnvironmentRow.last_used_at, EnvironmentRow.created_at)
    capable = [definition.type for definition in runtime.registry.environments.values() if supports(definition, phase)]
    if phase == "stopping":
        conditions = [
            EnvironmentRow.status == "ready",
            idle_past(EnvironmentRow.template_id, since, "stop_after_seconds"),
            # Stopping would clear the permanent failure that refuses its use, such as a lost sandbox's.
            or_(EnvironmentRow.failure.is_(None), ~EnvironmentRow.failure["permanent"].as_boolean()),
        ]
    else:
        conditions = [
            EnvironmentRow.status.in_(("reserved", "ready", "stopped")),
            idle_past(EnvironmentRow.template_id, since, "delete_after_seconds"),
            ~mounted(EnvironmentRow.id),
        ]
    async with transaction(runtime.storage) as session:
        candidates = (
            await session.scalars(
                select(EnvironmentRow)
                .where(
                    EnvironmentRow.template_id.is_not(None),
                    or_(
                        EnvironmentRow.status == "reserved",
                        EnvironmentRow.provider_identity["type"].astext.in_(capable),
                    ),
                    ~in_use(EnvironmentRow.id),
                    *conditions,
                )
                .order_by(since)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for environment in candidates:
            if await session.scalar(select(in_use(environment.id))) or (
                phase == "deleting" and await session.scalar(select(mounted(environment.id)))
            ):
                continue
            if environment.status == "reserved":
                environment.status = "deleted"
            else:
                await begin(session, environment, phase)
            record(
                session,
                WorkspaceScope(environment.organization_id, environment.workspace_id),
                actor_id=None,
                action="environment.stop" if phase == "stopping" else "environment.delete",
                target_kind="environment",
                target_id=environment.id,
                details={"reason": "idle"},
            )


async def _each(runtime: Runtime, due: Select[tuple[str]], call: Callable[[str], Awaitable[None]]) -> None:
    """Call `call` concurrently for at most `environments.batch` of the `due` instances; a failure is logged."""
    async with transaction(runtime.storage) as session:
        environment_ids = list(await session.scalars(due.limit(runtime.settings.environments.batch)))
    async with anyio.create_task_group() as group:
        for environment_id in environment_ids:
            group.start_soon(_logged, call, environment_id)


async def _logged(call: Callable[[str], Awaitable[None]], environment_id: str) -> None:
    try:
        await call(environment_id)
    except Exception as error:
        logger.warning(
            "Environment maintenance call failed",
            extra={
                "environment_id": environment_id,
                "error_type": type(error).__name__,
                "exception_details": exception_details(error),
            },
        )


def maintenance_sweep(runtime: Runtime) -> Sweep:
    settings = runtime.settings.environments
    owner = new_object_id("ctl")
    return Sweep(
        name="maintain_environments",
        every=settings.scan_seconds,
        run=partial(maintain_environments, runtime, owner=owner),
        # Two claims' worth: the dispatched calls are bounded by their deadlines and publication.
        timeout=settings.operation_seconds * 2 + 30,
    )


def renewal_sweep(runtime: Runtime) -> Sweep:
    settings = runtime.settings.environments
    return Sweep(
        name="renew_environments",
        every=settings.scan_seconds,
        run=partial(renew_environments, runtime),
        # One claim's worth: the dispatched renewals are bounded by their deadline and publication.
        timeout=settings.renewal_seconds + PUBLISH_SECONDS + 30,
    )
