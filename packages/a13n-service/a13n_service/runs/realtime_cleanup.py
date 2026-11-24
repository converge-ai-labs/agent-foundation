"""Recover accepted calls without ingress and close provider calls orphaned by a lost Attempt."""

import asyncio
from datetime import timedelta

from a13n_logging import get_logger
from pydantic import BaseModel
from pydantic_ai.realtime import WebRTCSession
from sqlalchemy import delete, select

from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.resources.models.realtime import open_realtime_model
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import ModelProviderRow
from a13n_service.runs.attempts import Lease, lock_thread_lease
from a13n_service.runs.realtime_transport import ATTACH_SECONDS
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RealtimeCallRow, RealtimeTicketRow, RunRow

logger = get_logger(__name__)


class ProviderCall(BaseModel):
    provider_id: str
    model_id: str
    model_key: str
    config: ModelConfig
    call_id: str


def location(run: RunRow) -> SecretLocation:
    return SecretLocation(run.organization_id, "realtime_calls", "provider_session", run.id)


async def provider_call(runtime: Runtime, lease: Lease, model: ResolvedModel, call_id: str | None) -> None:
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
            call.provider_session = runtime.keys.protect(value.model_dump_json().encode(), location(run)).model_dump(
                mode="json"
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
        ended = (
            await session.execute(
                select(RealtimeCallRow, RunRow)
                .join(RunRow, RunRow.id == RealtimeCallRow.run_id)
                .where(RunRow.sealed_at.is_not(None))
                .limit(limit)
                .with_for_update(of=RealtimeCallRow, skip_locked=True)
            )
        ).all()
        orphaned = [(run.id, location(run), call.provider_session) for call, run in ended if call.provider_session]
        for call, _ in ended:
            await session.delete(call)
    for workspace_id, run_id in stale:
        await _stop_abandoned(runtime, workspace_id, run_id)
    for run_id, aad, envelope in orphaned:
        try:
            value = ProviderCall.model_validate_json(runtime.keys.reveal(Envelope.model_validate(envelope), aad))
            async with short_session(runtime.storage) as session:
                provider = await read_provider(session, ModelProviderRow, value.provider_id)
            model = ResolvedModel(value.model_id, value.model_key, 0, value.config, None, provider)
            async with (
                asyncio.timeout(5),
                open_realtime_model(
                    model,
                    registry=runtime.registry,
                    keys=runtime.keys,
                    policy=runtime.endpoint_policy,
                    settings=runtime.settings.providers,
                ) as adapter,
            ):
                await adapter.hang_up(WebRTCSession(provider_name=adapter.system, session_id=value.call_id))
        except Exception as error:
            logger.warning(
                "Could not close orphaned provider call", extra={"run_id": run_id, "error_type": type(error).__name__}
            )
