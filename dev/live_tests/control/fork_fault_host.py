"""Fork and replacement-Attempt boundaries outside relational transactions."""

from functools import wraps


def install(faults):
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.interactions.queue_commands import QueuedRunCommands

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

    # Queue preparation follows source completion and precedes acceptance SQL.
    # Production drain deadlines still apply to pauses at this boundary.
    original_prepare = QueuedRunCommands.prepare_queued_run

    @wraps(original_prepare)
    async def prepare(self, **kwargs):
        result = await original_prepare(self, **kwargs)
        current = kwargs["current"]
        await faults.reach(
            "fork.handoff_prepared",
            run_id=current.id if current is not None else None,
            successor_run_id=result.run.id,
        )
        return result

    QueuedRunCommands.prepare_queued_run = prepare
