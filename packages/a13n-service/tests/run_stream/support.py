"""Explicit committed opening facts for presentation-only tests."""

from datetime import UTC, datetime

from a13n_service.run_stream import RedisRunStream, RunStreamEvent, deterministic_run_stream_event_id
from a13n_service.run_stream.domain import ActivationResult, RecoveryReason

ATTEMPT_ID = "rat_1234567890abcdef"
NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)


def opening_event(run_id: str, thread_id: str, *, attempt_id: str | None = None, number: int = 1) -> RunStreamEvent:
    kind = "run.accepted" if attempt_id is None else "run_attempt.leased"
    fact_id = "evt_" + deterministic_run_stream_event_id(kind, run_id, str(number))[4:]
    return RunStreamEvent(
        event_id=deterministic_run_stream_event_id("lifecycle", fact_id),
        event_type=kind,
        run_id=run_id,
        thread_id=thread_id,
        run_attempt_id=attempt_id,
        lifecycle_event_id=fact_id,
        occurred_at=NOW,
        payload={"data": {"attempt_number": number}},
    )


async def activate_stream(
    stream: RedisRunStream,
    organization_id: str,
    run_id: str,
    thread_id: str,
    *,
    attempt_id: str = ATTEMPT_ID,
    number: int = 1,
    reason: RecoveryReason | None = None,
) -> ActivationResult:
    await stream.initialize(
        organization_id,
        opening_event(run_id, thread_id),
        allow_create=True,
        expected_server_id=await stream.server_incarnation(),
    )
    return await stream.activate(
        organization_id,
        opening_event(run_id, thread_id, attempt_id=attempt_id, number=number),
        attempt_number=number,
        reason=reason,
        allow_create=True,
    )
