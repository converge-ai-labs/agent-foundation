"""Fork and replacement-Attempt boundaries outside relational transactions."""

from contextvars import ContextVar
from functools import wraps


def install(faults):
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.interactions.queue_handoff import CompletionQueueHandoffService
    from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter, _VerifiedQueueOutcome

    from .control_fault_host import acceptance_facts

    original_initial = RunAcceptanceService._publish_initial

    @wraps(original_initial)
    async def initial(self, run, state):
        if run.lineage_kind.value == "fork":
            await faults.reach("fork.initial_before", **acceptance_facts(run))
        return await original_initial(self, run, state)

    RunAcceptanceService._publish_initial = initial
    original_claim = RunStateStore.claim_writer

    @wraps(original_claim)
    async def claim(self, state, *, attempt_number):
        facts = {"run_id": state.envelope.run_id, "fence": attempt_number}
        await faults.reach("fork.writer_claim_before", **facts)
        result = await original_claim(self, state, attempt_number=attempt_number)
        await faults.reach("fork.writer_claim_after", **facts)
        return result

    RunStateStore.claim_writer = claim
    original_yield = AttemptExecutionService.yield_attempt

    @wraps(original_yield)
    async def yield_attempt(self, authority, reason):
        await faults.reach("fork.yield_before", run_id=authority.run_id, fence=authority.attempt_number)
        return await original_yield(self, authority, reason)

    AttemptExecutionService.yield_attempt = yield_attempt

    # Keep the pause outside both the preparation and commit budgets. Capture
    # the actual prepared successor in this verification call's own context.
    prepared_handoff = ContextVar("live_fork_handoff", default=None)
    original_prepare = CompletionQueueHandoffService.prepare_consumption

    @wraps(original_prepare)
    async def prepare(self, **kwargs):
        result = await original_prepare(self, **kwargs)
        prepared_handoff.set({"run_id": kwargs["authority"].run_id, "successor_run_id": kwargs["successor_run"].id})
        return result

    CompletionQueueHandoffService.prepare_consumption = prepare
    original_verify = DatabaseAttemptCommitter.verify_state_outcome

    @wraps(original_verify)
    async def verify(self, authority, state):
        token = prepared_handoff.set(None)
        try:
            result = await original_verify(self, authority, state)
            facts = prepared_handoff.get()
            if isinstance(result, _VerifiedQueueOutcome) and facts is not None:
                await faults.reach("fork.handoff_prepared", **facts)
            return result
        finally:
            prepared_handoff.reset(token)

    DatabaseAttemptCommitter.verify_state_outcome = verify
