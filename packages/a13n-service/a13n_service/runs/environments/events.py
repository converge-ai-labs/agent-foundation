"""Best-effort hints about committed Environment transitions."""

import asyncio
from functools import partial
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import after_commit
from a13n_service.infra.telemetry import meter
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime

STREAM_KEY = "a13n:environment-events:v1"
GROUP = "a13n-app-environments"
PUBLISH_DROPS = meter.create_counter("a13n.environment.events.publish.dropped", unit="{hint}")
type EnvironmentKind = Literal["managed", "external", "external_http"]
type EventKind = Literal["ready", "stopped", "deleted", "lost", "recreated"]


class EnvironmentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str
    workspace_id: str
    environment_id: str
    kind: EnvironmentKind
    event: EventKind
    origin: Literal["create", "start", "register"] | None = None
    reason: str | None = None
    app_id: str | None = None
    app_context: dict[str, str] | None = None
    replaces_environment_id: str | None = None


async def _publish(runtime: Runtime, event: EnvironmentEvent) -> None:
    async with asyncio.timeout(min(runtime.settings.redis.timeout, 0.5)):
        await runtime.redis.xadd(STREAM_KEY, {"data": event.model_dump_json()}, maxlen=10_000, approximate=True)


async def _handoff(runtime: Runtime, event: EnvironmentEvent) -> None:
    pending = runtime.environment_publish_tasks
    if runtime.tasks.stopping or len(pending) >= 32:
        PUBLISH_DROPS.add(1)
        return
    task = runtime.tasks.start(_publish(runtime, event), name="environment-event-publish")
    pending.add(task)
    task.add_done_callback(pending.discard)


def publish_after_commit(
    session: AsyncSession,
    runtime: Runtime,
    environment: EnvironmentRow,
    event: EventKind,
    *,
    origin: Literal["create", "start", "register"] | None = None,
    reason: str | None = None,
) -> None:
    if not runtime.has_environment_subscribers:
        return
    payload = EnvironmentEvent(
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment_id=environment.id,
        kind=cast(EnvironmentKind, environment.kind),
        event=event,
        origin=origin,
        reason=reason,
        app_id=environment.app_id,
        app_context=environment.app_context,
        replaces_environment_id=environment.replaces_environment_id,
    )
    after_commit(session, partial(_handoff, runtime, payload))
