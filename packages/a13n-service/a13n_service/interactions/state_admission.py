"""Bounded pre-Harness admission of the selected Attempt's state writer."""

from collections.abc import Awaitable, Callable

from a13n_logging import get_logger
from anyio import current_effective_deadline, current_time, fail_after, sleep

from a13n_service.storage import ObjectStoreUnavailable
from a13n_service.temporal import utc_now

from .attempts import AttemptAuthorityError, AttemptContext
from .domain import Run
from .objects import RunObjectIntegrityError, RunStateStore, StaleStateWriter, StoredRunState

logger = get_logger(__name__)

CLAIM_CYCLES = 4
CLAIM_REQUEST_SECONDS = 5.0
CLAIM_TOTAL_SECONDS = 30.0
CLAIM_BACKOFF_SECONDS = 0.1


class StateClaimExhausted(RuntimeError):
    """Retryable state admission exhausted its bounded local budget."""


async def claim_run_state(
    run: Run,
    states: RunStateStore,
    *,
    current_context: Callable[[], AttemptContext],
    validate_authority: Callable[[], Awaitable[None]],
) -> StoredRunState:
    """Reread complete objects on admission conflicts, never rebase Agent work."""

    context = current_context()
    reserve = min(1.0, context.renewal_timeout.total_seconds())
    total = min(CLAIM_TOTAL_SECONDS, current_effective_deadline() - current_time() - reserve)
    if run.recovery_budget.recovery_deadline_at is not None:
        total = min(total, (run.recovery_budget.recovery_deadline_at - utc_now()).total_seconds() - reserve)

    def request_timeout() -> float:
        remaining = (current_context().lease_expires_at - utc_now()).total_seconds()
        if remaining <= 0:
            raise AttemptAuthorityError("State admission lease expired")
        return max(
            0.0,
            min(CLAIM_REQUEST_SECONDS, remaining - reserve),
        )

    pending: StoredRunState | None = None
    try:
        with fail_after(max(0.0, total)):
            for cycle in range(CLAIM_CYCLES):
                # This lock is held only by the callback's short PG check. Reads,
                # writes, reconciliation and backoff must allow lease renewal.
                with fail_after(request_timeout()):
                    await validate_authority()
                try:
                    with fail_after(request_timeout()):
                        observed = await states.read_run(run)
                    if observed.writer_fence > context.fence:
                        raise AttemptAuthorityError("State writer belongs to a newer Attempt")
                    if pending is not None and observed.writer_fence == context.fence:
                        if (
                            observed.body != pending.body
                            or observed.digest_sha256 != pending.digest_sha256
                            or observed.info.version == pending.info.version
                        ):
                            raise RunObjectIntegrityError("Uncertain state claim does not match its exact publication")
                    with fail_after(request_timeout()):
                        await validate_authority()
                    if observed.writer_fence < context.fence:
                        pending = observed
                    with fail_after(request_timeout()):
                        claimed = await states.claim_writer(observed, fence=context.fence)
                    with fail_after(request_timeout()):
                        await validate_authority()
                    logger.info(
                        "run_state_writer_claimed",
                        extra={"run_id": run.id, "fence": context.fence, "claim_cycle": cycle + 1},
                    )
                    return claimed
                except (ObjectStoreUnavailable, TimeoutError, StaleStateWriter) as error:
                    logger.info(
                        "run_state_claim_retry",
                        extra={
                            "run_id": run.id,
                            "fence": context.fence,
                            "claim_cycle": cycle + 1,
                            "error_type": type(error).__name__,
                        },
                    )
                    if cycle + 1 == CLAIM_CYCLES:
                        raise StateClaimExhausted("State writer claim retry budget exhausted") from error
                    with fail_after(request_timeout()):
                        await sleep(CLAIM_BACKOFF_SECONDS * 2**cycle)
    except TimeoutError as error:
        raise StateClaimExhausted("State writer claim deadline exhausted") from error
    raise AssertionError("state claim must return or fail")
