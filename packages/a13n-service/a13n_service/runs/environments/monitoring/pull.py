"""One configured control writer reconciles protected collector fragments, without owning delivery."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Literal

from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from a13n_service.infra.db import short_session
from a13n_service.providers import collectors
from a13n_service.providers.collectors import Job, Kind
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.runs.environments.monitoring import collection
from a13n_service.runs.environments.monitoring.collection import Selection
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.tables import WorkspaceRow

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
type State = Literal["reload_requested", "not_ready", "unsupported", "disabled", "discovery_failed", "changed"]


class TargetStatus(BaseModel):
    revision: str | None
    state: State


class Receipt(BaseModel):
    # An acknowledgement of POST /-/reload is not proof that vmagent applied the
    # asynchronously loaded file, or that either collector scraped/exported data.
    state: Literal["reload_requested", "failed"]
    checked_at: float
    targets: dict[str, TargetStatus] = Field(default_factory=dict)
    truncated: bool = False


def receipt_key(kind: Kind) -> str:
    return f"a13n:metrics:collector:{kind}"


async def selections(runtime: Runtime, kind: Kind) -> tuple[list[Selection], bool, dict[str, TargetStatus]]:
    """Return bounded, detached inputs. Provider discovery happens after closing the session."""
    limit = runtime.settings.monitoring.collector_targets
    scan_budget = 4 * limit
    scanned = 0
    selected: list[Selection] = []
    failures: dict[str, TargetStatus] = {}
    after = ""
    while True:
        async with short_session(runtime.storage) as session:
            rows = (
                await session.execute(
                    select(EnvironmentRow, DataSourceRow)
                    .join(DataSourceRow, DataSourceRow.id == EnvironmentRow.metrics_data_source_id)
                    .join(WorkspaceRow, WorkspaceRow.id == EnvironmentRow.workspace_id)
                    .where(
                        EnvironmentRow.id > after,
                        EnvironmentRow.status == "ready",
                        DataSourceRow.enabled.is_(True),
                        DataSourceRow.config["collection"].astext == "pull",
                        DataSourceRow.config["collector"].astext == kind,
                        WorkspaceRow.archived_at.is_(None),
                    )
                    .order_by(EnvironmentRow.id)
                    .limit(min(limit + 1, scan_budget - scanned + 1))
                )
            ).all()
        for env, source in rows:
            if scanned == scan_budget:
                return selected, True, failures
            scanned += 1
            try:
                # Each target owns its SQL scope: a failed query cannot poison the next target.
                async with short_session(runtime.storage) as session:
                    item = await collection.selected(runtime, session, env, source)
            except Exception as error:
                if len(failures) < limit:
                    failures[env.id] = TargetStatus(revision=None, state="discovery_failed")
                logger.warning(
                    "Collector target selection failed",
                    extra={"environment_id": env.id, "exception_details": exception_details(error)},
                )
                continue
            if item.target is not None:
                if len(selected) == limit:
                    return selected, True, failures
                selected.append(item)
        if len(rows) <= limit:
            return selected, False, failures
        # Unsupported recipes and disabled providers do not consume target slots.
        after = rows[-1][0].id


async def _reconcile(runtime: Runtime, kind: Kind) -> None:
    config = runtime.settings.monitoring.collectors[kind]
    if not config.writer:
        return
    settings = runtime.settings.monitoring
    async with collectors.writer_lock(config, kind) as acquired:
        if not acquired:
            return
        selected, truncated, statuses = await selections(runtime, kind)
        slots = asyncio.Semaphore(8)

        async def discover(item: Selection) -> Job | None:
            if item.target is None:
                state: State = (
                    "disabled"
                    if item.status == "disabled"
                    else ("not_ready" if item.status == "not_ready" else "unsupported")
                )
                statuses[item.target_id] = TargetStatus(revision=item.revision, state=state)
                return None
            async with slots:
                try:
                    async with asyncio.timeout(settings.request_seconds):
                        target = await collection.scrape(runtime, item)
                except NotImplementedError:
                    statuses[item.target_id] = TargetStatus(revision=item.revision, state="unsupported")
                    return None
                except Exception as error:
                    statuses[item.target_id] = TargetStatus(revision=item.revision, state="discovery_failed")
                    logger.warning(
                        "Collector target discovery failed",
                        extra={"environment_id": item.target_id, "exception_details": exception_details(error)},
                    )
                    return None
            statuses[item.target_id] = TargetStatus(revision=item.revision, state="reload_requested")
            return Job(item.target_id, item.workspace_id, item.organization_id, target)

        jobs: list[Job | None] = [None] * len(selected)

        async def discover_at(index: int, item: Selection) -> None:
            jobs[index] = await discover(item)

        # A task group joins every provider cleanup before releasing the writer lock.
        async with asyncio.TaskGroup() as tasks:
            for index, item in enumerate(selected):
                tasks.create_task(discover_at(index, item))
        current, current_truncated, failures = await selections(runtime, kind)
        statuses.update(failures)
        revisions = {item.target_id: item.revision for item in current if item.target is not None}
        valid = []
        for job in jobs:
            if job is None:
                continue
            status = statuses[job.environment_id]
            if status.state != "reload_requested":
                continue
            if revisions.get(job.environment_id) != status.revision:
                status.state = "changed"
            else:
                valid.append(job)
        content = collectors.render(
            kind,
            valid,
            receiver=config.receiver,
            interval=settings.poll_seconds,
            timeout=min(settings.poll_seconds, settings.request_seconds),
        )
        receipt = Receipt(
            state="reload_requested",
            checked_at=time.time(),
            targets=statuses,
            truncated=truncated or current_truncated,
        )
        try:
            async with asyncio.timeout(settings.request_seconds):
                await collectors.apply(runtime.endpoint_policy, config, kind, content, timeout=settings.request_seconds)
        except Exception as error:
            receipt.state = "failed"
            logger.warning(
                "Collector reload failed", extra={"collector": kind, "exception_details": exception_details(error)}
            )
        receipt.checked_at = time.time()
        await runtime.redis.set(receipt_key(kind), receipt.model_dump_json(), ex=int(settings.collector_seconds * 3))


async def reconcile(runtime: Runtime, kind: Kind) -> None:
    try:
        await _reconcile(runtime, kind)
    except Exception as error:
        logger.warning(
            "Collector reconciliation failed",
            extra={"collector": kind, "exception_details": exception_details(error)},
        )
        receipt = Receipt(state="failed", checked_at=time.time())
        await runtime.redis.set(
            receipt_key(kind), receipt.model_dump_json(), ex=int(runtime.settings.monitoring.collector_seconds * 3)
        )


async def run(runtime: Runtime, kind: Kind) -> None:
    while True:
        try:
            await reconcile(runtime, kind)
        except Exception as error:
            # Redis failures cannot be recorded there; retry on the next reconciliation.
            logger.warning(
                "Collector receipt unavailable",
                extra={"collector": kind, "exception_details": exception_details(error)},
            )
        await asyncio.sleep(runtime.settings.monitoring.collector_seconds)
