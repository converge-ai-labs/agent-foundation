"""Entity-owned request keys and atomic acceptance rollback."""

from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.interactions.control_domain import RunAcceptanceReceipt
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from anyio import Event, create_task_group, fail_after
from sqlalchemy import select

from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import NOW, WORKSPACE_ID
from tests.interactions.conftest import interaction_sessions as interaction_sessions

from .test_commands import _actor, _commands, _Freezing, _frozen, _Preparation, _request

pytestmark = pytest.mark.anyio


async def test_http_start_key_survives_twenty_four_hours(
    lifecycle_interaction_sessions,
    tmp_path,
) -> None:
    objects = await LocalObjectStore.create(tmp_path / "expiry-objects")
    now = NOW
    commands = _commands(
        lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]), clock=lambda: now
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    request = _request("finite replay")
    first = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="expiry", request=request
    )
    now = NOW + timedelta(hours=24, microseconds=-1)
    assert (
        await commands.runs.start(actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="expiry", request=request)
        == first
    )
    now = NOW + timedelta(hours=24)
    second = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="expiry", request=request
    )
    assert second.run_id == first.run_id
    async with short_session(lifecycle_interaction_sessions) as database:
        assert await database.get(RunRecord, first.run_id) is not None


async def test_binding_failure_rolls_back_run_lifecycle_and_http_receipt(
    lifecycle_interaction_sessions, tmp_path
) -> None:
    from a13n_service.durable_operations.models import OutboxRecord
    from a13n_service.lifecycle.models import LifecycleEventRecord

    objects = await LocalObjectStore.create(tmp_path / "rollback-objects")
    commands = _commands(lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]))
    await seed_hook_actor_access(lifecycle_interaction_sessions)

    async def fail_binding(database, receipt):
        assert await database.get(RunRecord, receipt.run_id) is not None
        raise RuntimeError("binding rejected")

    with pytest.raises(RuntimeError, match="binding rejected"):
        await commands.runs.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="rollback",
            request=_request("atomic"),
            transaction_hook=fail_binding,
        )
    async with short_session(lifecycle_interaction_sessions) as database:
        for model in (RunRecord, LifecycleEventRecord, OutboxRecord, IdempotencyEvidenceRecord):
            assert (await database.scalars(select(model))).all() == []


async def test_concurrent_start_reconciles_to_one_committed_receipt(interaction_sessions, tmp_path):
    sessions = interaction_sessions
    objects = await LocalObjectStore.create(tmp_path / "race-objects")
    both_preparing = Event()

    class ConcurrentPreparation(_Preparation):
        async def prepare(self, **kwargs):
            prepared = await super().prepare(**kwargs)
            if self.calls == 2:
                both_preparing.set()
            await both_preparing.wait()
            return prepared

    preparation = ConcurrentPreparation()
    commands = _commands(sessions, objects, preparation, _Freezing([_frozen()]))
    await seed_hook_actor_access(sessions)
    receipts: list[RunAcceptanceReceipt] = []
    bindings: list[str] = []

    async def bind(database, receipt):
        assert await database.get(RunRecord, receipt.run_id) is not None
        bindings.append(receipt.run_id)

    async def submit():
        receipts.append(
            await commands.runs.start(
                actor=_actor(),
                workspace_id=WORKSPACE_ID,
                idempotency_key="concurrent-start",
                request=_request(),
                transaction_hook=bind,
            )
        )

    with fail_after(15):
        async with create_task_group() as tasks:
            tasks.start_soon(submit)
            tasks.start_soon(submit)
    assert preparation.calls == 2
    assert receipts[0] == receipts[1]
    async with short_session(sessions) as database:
        runs = (await database.scalars(select(RunRecord))).all()
        evidence = (await database.scalars(select(IdempotencyEvidenceRecord))).all()
    assert [run.id for run in runs] == [receipts[0].run_id]
    assert evidence == []
    assert runs[0].request_key is not None

    binding_count = len(bindings)
    await submit()
    assert receipts[2] == receipts[0]
    assert len(bindings) == binding_count
    assert preparation.calls == 2
