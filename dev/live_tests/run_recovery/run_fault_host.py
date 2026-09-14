"""Opt-in fault hooks around real Service operations in disposable test Hosts.

Wrappers call the original implementation. Barriers are outside SQL transactions;
the queue rollback injection raises inside the real transaction without waiting.
No hook writes Run, Attempt, Thread or inbox lifecycle state.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import replace
from datetime import timedelta
from functools import wraps
from pathlib import Path

from ..infrastructure.run_faults import Faults


def install(config, role):
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.interactions.initialization import NewRunPolicy
    from a13n_service.interactions.objects import RUN_STATE_CONTENT_TYPE, RunStateStore
    from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter
    from a13n_service.storage.object_store.s3 import S3ObjectStore

    faults = Faults(Path(config["workspace_root"]).parent / "faults", role)
    if role == "control" and config["run_faults"].get("identity_management"):
        from ..iam.run_fault_identity import install_management_identity

        install_management_identity()
    original_put = S3ObjectStore.put

    @wraps(original_put)
    async def put(self, key, source, **kwargs):
        metadata = kwargs.get("metadata") or {}
        facts = None
        if kwargs.get("content_type") == RUN_STATE_CONTENT_TYPE and isinstance(source, bytes):
            try:
                kind = json.loads(source)["checkpoint_kind"]
            except (ValueError, KeyError):
                kind = "damaged"
            facts = {
                "run_id": metadata["run-id"],
                "kind": kind,
                "seq": int(metadata["checkpoint-seq"]),
                "fence": int(metadata["writer-fence"]),
            }
            await faults.reach("state.put_before", **facts)
        result = await original_put(self, key, source, **kwargs)
        if facts is not None:
            await faults.reach("state.put_after", **facts)
        return result

    S3ObjectStore.put = put
    original_read = RunStateStore.read

    @wraps(original_read)
    async def read(self, organization_id, run_id, **kwargs):
        if organization_id == config["organization_id"]:
            await faults.reach("state.read_before", run_id=run_id)
        return await original_read(self, organization_id, run_id, **kwargs)

    RunStateStore.read = read
    original_replace = RunStateStore.replace

    @wraps(original_replace)
    async def checkpoint(self, state, successor, **kwargs):
        facts = {
            "run_id": successor.run_id,
            "kind": successor.checkpoint_kind,
            "seq": successor.checkpoint_seq,
            "fence": kwargs["attempt_number"],
            "receipts": len(successor.host.inbox_receipts),
        }
        await faults.reach("checkpoint.before", **facts)
        result = await original_replace(self, state, successor, **kwargs)
        await faults.reach("checkpoint.after", **facts)
        return result

    RunStateStore.replace = checkpoint
    original_verify = DatabaseAttemptCommitter.verify_state_outcome

    @wraps(original_verify)
    async def verify(self, authority, state):
        result = await original_verify(self, authority, state)
        await faults.reach(
            "outcome.verified",
            run_id=authority.run_id,
            fence=authority.attempt_number,
            kind=state.envelope.checkpoint_kind,
        )
        return result

    DatabaseAttemptCommitter.verify_state_outcome = verify
    original_accept = RunAcceptanceService.accept_new_thread

    @wraps(original_accept)
    async def accept(self, **kwargs):
        result = await original_accept(self, **kwargs)
        await faults.reach("accept.committed", run_id=result.run_id)
        return result

    RunAcceptanceService.accept_new_thread = accept
    original_initial = RunAcceptanceService._publish_initial

    @wraps(original_initial)
    async def initial(self, run, state):
        result = await original_initial(self, run, state)
        await faults.reach("accept.state_published", run_id=run.id)
        return result

    RunAcceptanceService._publish_initial = initial
    # The accepted policy has no public deadline/usage setter. Supply a test Host
    # policy before acceptance, rather than editing durable budgets after acceptance.
    policy = config["run_faults"].get("policy", {})
    if policy:
        original_create = NewRunPolicy.create

        @wraps(original_create)
        def create(self, **kwargs):
            from a13n_service.interactions.domain import ExecutionBudget

            updates = dict(policy)
            seconds = updates.pop("deadline_seconds", None)
            if seconds is not None:
                updates["execution_deadline_at"] = kwargs["now"] + timedelta(seconds=seconds)
            budget = ExecutionBudget.model_validate({**self.execution_budget.model_dump(), **updates})
            return original_create(replace(self, execution_budget=budget), **kwargs)

        NewRunPolicy.create = create
    if not config["run_faults"].get("skills"):
        _install_queue_faults(faults)
    _install_attempt_faults(faults, config["run_faults"])
    if "control" in config["run_faults"]:
        from ..control.control_fault_host import install as install_control

        install_control(faults, config["run_faults"]["control"])
    if config["run_faults"].get("skills"):
        from ..skills.fault_host import install as install_skills

        install_skills(faults)


def _install_attempt_faults(faults, options):
    from a13n_service.interactions.attempts import AttemptExecutionService

    original_fail = AttemptExecutionService.fail

    @wraps(original_fail)
    async def fail(self, authority, failure, **kwargs):
        if kwargs.get("retryable") and "retry_after_seconds" in options:
            kwargs["retry_after"] = timedelta(seconds=options["retry_after_seconds"])
        result = await original_fail(self, authority, failure, **kwargs)
        await faults.reach("attempt.failed", run_id=authority.run_id, fence=authority.attempt_number)
        return result

    AttemptExecutionService.fail = fail


def _install_queue_faults(faults):
    from a13n_service.interactions import queue_handoff
    from a13n_service.storage import ObjectStoreUnavailable

    rollback = ContextVar("live_queue_rollback", default=False)

    original_prepare = queue_handoff.CompletionQueueHandoffService.prepare_consumption

    @wraps(original_prepare)
    async def prepare(self, **kwargs):
        commit = await original_prepare(self, **kwargs)
        authority = kwargs["authority"]
        facts = {"run_id": authority.run_id, "successor_run_id": kwargs["successor_run"].id}

        async def committed():
            await faults.reach("queue.before_commit", **facts)
            ticket = await faults.take("queue.rollback", **facts)
            if ticket is not None and ticket.rule.action != "unavailable":
                raise ValueError("Queue transaction faults only permit immediate failure")
            token = rollback.set(ticket is not None)
            try:
                result = await commit()
            except ObjectStoreUnavailable:
                if ticket is not None:
                    # The exception has already rolled back the real SQL transaction.
                    await faults.reach("queue.rolled_back", **facts)
                    await ticket.apply()
                raise
            finally:
                rollback.reset(token)
            await faults.reach("queue.after_commit", **facts)
            return result

        return committed

    queue_handoff.CompletionQueueHandoffService.prepare_consumption = prepare
    original_consume = queue_handoff._consume_queue_head

    @wraps(original_consume)
    async def consume(*args, **kwargs):
        result = await original_consume(*args, **kwargs)
        if rollback.get():
            raise ObjectStoreUnavailable("Injected failure after SQL queue consumption, before COMMIT")
        return result

    queue_handoff._consume_queue_head = consume
