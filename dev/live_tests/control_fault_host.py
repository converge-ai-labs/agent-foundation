"""Opt-in control barriers around real operations, never inside SQL transactions."""

from functools import wraps


def install(faults, options):
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.interactions.active_commands import ActiveRunCommands
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, ThreadInboxStore
    from a13n_service.interactions.outcomes import RunOutcomeService
    from a13n_service.interactions.queue_commands import QueuedRunCommands
    from a13n_service.interactions.scheduling import AttemptScheduler

    original_initial = RunAcceptanceService._publish_initial

    @wraps(original_initial)
    async def initial(self, run, state):
        result = await original_initial(self, run, state)
        await faults.reach("control.state_published", **acceptance_facts(run))
        return result

    RunAcceptanceService._publish_initial = initial

    for method, point in (
        ("advance_thread", "control.advance_committed"),
        ("accept_new_thread", "control.branch_committed"),
    ):
        _wrap_acceptance(RunAcceptanceService, method, point, faults)

    original_claim = AttemptScheduler.claim

    @wraps(original_claim)
    async def claim(self, run_id, worker):
        await faults.reach("control.before_claim", run_id=run_id)
        result = await original_claim(self, run_id, worker)
        await faults.reach("control.after_claim", run_id=run_id, claimed=result is not None)
        return result

    AttemptScheduler.claim = claim
    original_recover = QueuedRunCommands.recover_queued

    @wraps(original_recover)
    async def recover(self, **kwargs):
        await faults.reach("control.queue_recovery", thread_id=kwargs["thread"].id)
        return await original_recover(self, **kwargs)

    QueuedRunCommands.recover_queued = recover
    original_cancel = RunOutcomeService.cancel

    @wraps(original_cancel)
    async def cancel(self, **kwargs):
        await faults.reach("control.interrupt_prepared", run_id=kwargs["run_id"])
        return await original_cancel(self, **kwargs)

    RunOutcomeService.cancel = cancel
    for method in ("steer", "interrupt"):
        _wrap_active_command(ActiveRunCommands, method, faults)

    original_steer = ThreadInboxStore.append_steer

    @wraps(original_steer)
    async def steer(self, **kwargs):
        await faults.reach("control.steer_prepared", run_id=kwargs["run_id"])
        return await original_steer(self, **kwargs)

    ThreadInboxStore.append_steer = steer
    original_fail = AttemptExecutionService.fail

    @wraps(original_fail)
    async def fail(self, authority, failure, **kwargs):
        await faults.reach(
            "control.failure_prepared",
            run_id=authority.run_id,
            retryable=kwargs["retryable"],
            code=failure.code,
        )
        return await original_fail(self, authority, failure, **kwargs)

    AttemptExecutionService.fail = fail
    original_confirm = DatabaseThreadInboxReconciler.confirm_inbox_receipts

    @wraps(original_confirm)
    async def confirm(self, authority, state):
        result = await original_confirm(self, authority, state)
        await faults.reach(
            "control.inbox_consumed",
            run_id=authority.run_id,
            receipts=len(state.envelope.host.inbox_receipts),
            seq=state.envelope.checkpoint_seq,
        )
        return result

    DatabaseThreadInboxReconciler.confirm_inbox_receipts = confirm
    # Production constructors own these policy limits; the public API has no
    # setter. Only this lab lowers them, before any admission takes place.
    if limits := options.get("inbox"):
        original_init = ThreadInboxStore.__init__

        @wraps(original_init)
        def inbox(self, *args, **kwargs):
            return original_init(self, *args, **{**kwargs, **limits})

        ThreadInboxStore.__init__ = inbox

    from .fork_fault_host import install as install_fork
    from .queue_fault_host import install as install_queue

    install_queue(faults, options)
    install_fork(faults)


def acceptance_facts(run):
    return {
        "run_id": run.id,
        "thread_id": run.thread_id,
        "input_kind": run.input_kind.value,
        "lineage_kind": run.lineage_kind.value,
        "parent_run_id": run.parent_run_id or "",
        "retry_of_run_id": run.retry_of_run_id or "",
    }


def _wrap_acceptance(owner, method, point, faults):
    original = getattr(owner, method)

    @wraps(original)
    async def accepted(self, **kwargs):
        result = await original(self, **kwargs)
        await faults.reach(point, **acceptance_facts(kwargs["run"]))
        return result

    setattr(owner, method, accepted)


def _wrap_active_command(owner, method, faults):
    original = getattr(owner, method)

    @wraps(original)
    async def command(self, **kwargs):
        result = await original(self, **kwargs)
        await faults.reach("control." + method + "_committed", run_id=kwargs["run_id"])
        return result

    setattr(owner, method, command)
