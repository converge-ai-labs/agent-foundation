"""Best-effort, post-commit Run seal hints shared across Service roles."""

import asyncio
from functools import partial

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import after_commit
from a13n_service.infra.telemetry import meter
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Sealed
from a13n_service.runs.tables import RunRow

PUBLISH_DROPS = meter.create_counter("a13n.run_seal.publish.dropped", unit="{hint}")


def channel(runtime: Runtime) -> str:
    """Pub/Sub ignores Redis SELECT; route hints by the configured logical database."""
    db = runtime.redis.connection_pool.connection_kwargs.get("db", 0)
    return f"a13n:run-seals:v1:db:{db}"


class RunSealEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    principal_id: str
    status: Sealed
    labels: dict[str, str]


async def _publish(runtime: Runtime, event: RunSealEvent) -> None:
    # A lost hint is recovered by the App's sweep; settlement never waits on App code.
    async with asyncio.timeout(min(runtime.settings.redis.timeout, 0.5)):
        await runtime.redis.publish(channel(runtime), event.model_dump_json())


async def _handoff(runtime: Runtime, event: RunSealEvent) -> None:
    pending = runtime.seal_publish_tasks
    if runtime.tasks.stopping or len(pending) >= 32:
        PUBLISH_DROPS.add(1)
        return
    task = runtime.tasks.start(_publish(runtime, event), name="run-seal-publish")
    pending.add(task)
    task.add_done_callback(pending.discard)


async def publish_after_commit(session: AsyncSession, runtime: Runtime, run: RunRow, status: Sealed) -> None:
    if not any(app.has_run_seal_subscribers for app in runtime.apps.apps):
        return
    event = RunSealEvent(
        organization_id=run.organization_id,
        workspace_id=run.workspace_id,
        session_id=run.session_id,
        thread_id=run.thread_id,
        run_id=run.id,
        principal_id=run.principal_id,
        status=status,
        labels=dict((await session.scalar(select(RunRow.labels).where(RunRow.id == run.id))) or {}),
    )
    after_commit(session, partial(_handoff, runtime, event))
