from __future__ import annotations

import pytest
from a13n_harness import SafeFailure
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.run_stream.display_candidates import DisplayCandidates
from a13n_service.storage import transaction
from sqlalchemy import select, update
from tests.hooks.support import RUN_ID, seed_run_and_secret
from tests.interactions.conftest import NOW
from tests.lifecycle_support import test_lifecycle_writer

pytestmark = pytest.mark.anyio


async def test_keyset_discovery_includes_terminal_runs_and_waits_for_all_lifecycle_facts(
    lifecycle_interaction_sessions,
):
    sessions = lifecycle_interaction_sessions
    await seed_run_and_secret(sessions)
    terminal_id = "run_2222222222222222"
    async with transaction(sessions) as database:
        source = await database.get(RunRecord, RUN_ID)
        values = {column.name: getattr(source, column.name) for column in RunRecord.__table__.columns}
        values.update(
            id=terminal_id,
            status="failed",
            sealed_at=NOW,
            failure_json=SafeFailure(code="test_failure", message="Failed before execution.").model_dump(mode="json"),
        )
        terminal = RunRecord(**values)
        database.add(terminal)
        await database.flush()
        for index, kind in enumerate(("run.accepted", "run.failed")):
            await test_lifecycle_writer().append_run_lifecycle(
                database,
                terminal,
                kind,
                mutation_id=f"mut_{index:016d}",
                occurred_at=NOW,
                actor_type="worker",
                actor_id="worker-1",
            )
    discovery = DisplayCandidates(sessions)
    first = await discovery.page(after_run_id=None, limit=1)
    second = await discovery.page(after_run_id=first[0].run_id, limit=1)
    assert {first[0].run_id, second[0].run_id} == {RUN_ID, terminal_id}
    assert await discovery.page(after_run_id=second[0].run_id, limit=1) == ()
    candidate = next(item for item in (*first, *second) if item.run_id == terminal_id)
    assert (await discovery.settlement(candidate)).closed_at is None
    async with transaction(sessions) as database:
        await database.execute(
            update(LifecycleEventRecord).values(
                projection_state="projected",
                projection_next_attempt_at=None,
                projected_at=NOW,
            )
        )
    settled = await discovery.settlement(candidate)
    assert settled.accepted_projected and settled.closed_at == NOW and not settled.abandoned
    async with transaction(sessions) as database:
        accepted = await database.scalar(
            select(LifecycleEventRecord).where(LifecycleEventRecord.event_type == "run.accepted")
        )
        accepted.projection_state = "abandoned"
        accepted.projected_at = None
    retired = await discovery.settlement(candidate)
    assert not retired.accepted_projected and retired.abandoned and retired.closed_at == NOW
