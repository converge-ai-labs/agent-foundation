"""Recover accepted calls without ingress and close provider calls orphaned by a lost Attempt."""

import asyncio
from dataclasses import replace
from datetime import timedelta

from a13n_logging import get_logger
from pydantic import BaseModel
from pydantic_ai.realtime import WebRTCSession
from sqlalchemy import JSON, delete, func, or_, select

from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.telemetry import meter
from a13n_service.resources.models.realtime import open_realtime_model
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import ModelProviderRow
from a13n_service.runs.attempts import Lease, LeaseLost, lock_thread_lease
from a13n_service.runs.realtime_transport import ATTACH_SECONDS
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RealtimeCallRow, RealtimeTicketRow, RunRow

logger = get_logger(__name__)
ORPHAN_HANGUPS = meter.create_counter(
    "a13n.realtime.orphan_hangups", unit="{call}", description="Orphaned WebRTC provider call cleanup attempts"
)


class ProviderCall(BaseModel):
    provider_id: str
    model_id: str
    model_key: str
    config: ModelConfig
    call_id: str


def location(run: RunRow) -> SecretLocation:
    return SecretLocation(run.organization_id, "realtime_calls", "provider_session", run.id)


async def provider_call(runtime: Runtime, lease: Lease, model: ResolvedModel, call_id: str | None) -> None:
    try:
        async with transaction(runtime.storage) as session:
            _, run, _, _ = await lock_thread_lease(session, lease)
            call = await session.get_one(RealtimeCallRow, run.id)
            if call_id is None:
                call.provider_session = None
            else:
                value = ProviderCall(
                    provider_id=model.provider.id,
                    model_id=model.id,
                    model_key=model.key,
                    config=model.config,
                    call_id=call_id,
                )
                call.provider_session = runtime.keys.protect(
                    value.model_dump_json().encode(), location(run)
                ).model_dump(mode="json")
    except LeaseLost:
        raise
    except Exception as error:
        if call_id is not None:
            raise
        logger.warning(
            "Provider hangup record retained for cleanup",
            extra={"run_id": lease.run_id, "error_type": type(error).__name__},
        )


async def reconcile(runtime: Runtime) -> None:
    from a13n_service.runs.realtime import _stop_abandoned

    limit = runtime.settings.control.sweep_batch
    async with transaction(runtime.storage) as session:
        current = await now(session)
        expired = (
            select(RealtimeTicketRow.digest)
            .where(RealtimeTicketRow.expires_at <= current)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        await session.execute(delete(RealtimeTicketRow).where(RealtimeTicketRow.digest.in_(expired)))
        stale = (
            await session.execute(
                select(RunRow.workspace_id, RunRow.id)
                .where(
                    RunRow.trigger == "realtime",
                    RunRow.status == "accepted",
                    RunRow.available_at < current - timedelta(seconds=ATTACH_SECONDS),
                )
                .limit(limit)
            )
        ).all()
        sealed = select(RealtimeCallRow.run_id).join(RunRow).where(RunRow.sealed_at.is_not(None))
        finished = (
            sealed.where(or_(RealtimeCallRow.provider_session.is_(None), RealtimeCallRow.provider_session == JSON.NULL))
            .limit(limit)
            .with_for_update(of=RealtimeCallRow, skip_locked=True)
        )
        await session.execute(delete(RealtimeCallRow).where(RealtimeCallRow.run_id.in_(finished)))
    for workspace_id, run_id in stale:
        try:
            await _stop_abandoned(runtime, workspace_id, run_id)
        except ServiceError as error:
            if error.code != "not_found":
                raise
    for _ in range(limit):
        async with transaction(runtime.storage) as session:
            current = await now(session)
            due = func.coalesce(RealtimeCallRow.hangup_retry_at, RunRow.sealed_at)
            row = (
                await session.execute(
                    select(RealtimeCallRow, RunRow)
                    .join(RunRow)
                    .where(RunRow.sealed_at.is_not(None), RealtimeCallRow.provider_session != JSON.NULL, due <= current)
                    .order_by(due, RunRow.id)
                    .limit(1)
                    .with_for_update(of=RealtimeCallRow, skip_locked=True)
                )
            ).one_or_none()
            if row is None:
                return
            call, run = row
            # Other Controls can retry after a crashed or timed-out sweep.
            call.hangup_retry_at = current + timedelta(seconds=max(30, runtime.settings.worker.lease_seconds))
            run_id, aad, envelope = run.id, location(run), call.provider_session

        try:
            async with asyncio.timeout(5):
                value = ProviderCall.model_validate_json(runtime.keys.reveal(Envelope.model_validate(envelope), aad))
                async with short_session(runtime.storage) as session:
                    provider = await read_provider(session, ModelProviderRow, value.provider_id)
                # Inference headers are irrelevant to hanging up an existing provider call.
                provider = replace(
                    provider,
                    extra_headers={},
                    config={k: v for k, v in provider.config.items() if k != "session_affinity_header"},
                )
                model = ResolvedModel(value.model_id, value.model_key, 0, value.config, None, provider)
                async with open_realtime_model(
                    model,
                    registry=runtime.registry,
                    keys=runtime.keys,
                    policy=runtime.endpoint_policy,
                    settings=runtime.settings.providers,
                ) as adapter:
                    await adapter.hang_up(WebRTCSession(provider_name=adapter.system, session_id=value.call_id))
        except Exception as error:
            ORPHAN_HANGUPS.add(1, {"result": "failed"})
            logger.warning(
                "Could not close orphaned provider call", extra={"run_id": run_id, "error_type": type(error).__name__}
            )
        else:
            # A repeated hangup of an already ended provider call is idempotent.
            async with transaction(runtime.storage) as session:
                await session.execute(delete(RealtimeCallRow).where(RealtimeCallRow.run_id == run_id))
            ORPHAN_HANGUPS.add(1, {"result": "succeeded"})
