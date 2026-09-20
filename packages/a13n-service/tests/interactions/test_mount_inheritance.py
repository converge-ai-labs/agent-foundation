"""Retained successor acceptance atomically copies facts and clears live evidence."""

from datetime import timedelta

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.environments.mount_inheritance import inherit_run_mounts
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.control_domain import normalize_feedback
from a13n_service.interactions.domain import RunInputKind, RunLineageKind
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_retry_state,
    initialize_waiting_continuation_state,
)
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc
from sqlalchemy import func, select

from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory

from .conftest import NOW, WORKSPACE_ID
from .mount_helpers import accepted_mount
from .test_attempt_execution import _accept_root, _authority, _waiting_state, _worker
from .test_environment_retention import cancel
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("kind", ["retry", "waiting_feedback", "waiting_continue"])
async def test_successor_acceptance_copies_mounts_atomically_with_fresh_observations(
    interaction_sessions,
    interaction_object_store,
    client_environment,
    kind,
):
    sessions = interaction_sessions
    _, _, environment = client_environment
    states, source, initial = await _accept_root(sessions, interaction_object_store)
    claim = await AttemptScheduler(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        source.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    async with transaction(sessions) as database:
        database.add_all(
            [
                accepted_mount(
                    source.id,
                    environment.id,
                    name="first",
                    use_started_at=NOW,
                    application_status="ready",
                    applied_attempt_id=claim.attempt.id,
                    applied_attempt_fence=claim.attempt.attempt_number,
                    observed_at=NOW,
                ),
                accepted_mount(source.id, environment.id, name="second", created_at=NOW + timedelta(microseconds=1)),
            ]
        )
    seed = RunStateSeed(
        run_id="run_7777777777777777",
        agent_id=source.agent_id,
        agent_revision_id=source.agent_revision_id,
        effective_agent_config=initial.effective_agent_config,
    )
    changes = {
        "id": seed.run_id,
        "idempotency_key": "successor",
    }
    if kind == "retry":
        await cancel(sessions, interaction_object_store, source, NOW + timedelta(seconds=1))
        state = initialize_retry_state(
            seed,
            thread_id=source.thread_id,
            source_lineage_kind=source.lineage_kind,
            source_input_kind=source.input_kind,
            parent=None,
        )
        changes["retry_of_run_id"] = source.id
    else:
        authority = _authority(claim)
        execution = AttemptExecutionService(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
        preparation = await execution.commit_preparation_success(authority)
        await execution.enter_harness(authority, preparation=preparation, harness_run_id="waiting-mounts")
        waiting = _waiting_state(initial, claim.attempt.id, claim.attempt.attempt_number)
        stored = await execution.publish_checkpoint(
            authority, states, await states.read(source.organization_id, source.id), waiting
        )
        await RunOutcomeService(
            sessions, RunPayloadStore(interaction_object_store), clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).commit_state_outcome(authority, stored)
        feedback = normalize_feedback(
            waiting_run_id=source.id,
            sealed_state_digest_sha256=stored.digest_sha256,
            pending=waiting.outcome_candidate.pending,
            submitted=(),
        ).model_dump(mode="json")
        if kind == "waiting_continue":
            feedback["input"] = source.input
        changes.update(
            parent_run_id=source.id,
            lineage_kind=RunLineageKind.continue_,
            input_kind=RunInputKind(kind),
            input=feedback,
            input_text=None,
        )
        state = initialize_waiting_continuation_state(seed, waiting)
    successor = source.model_copy(update=changes)
    async with short_session(sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        version, head = thread.version, thread.head_run_id
    acceptance = RunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(interaction_object_store),
        InlineHookValidator(EndpointPolicy()),
        bindings=ordinary_memory(sessions),
        clock=lambda: NOW,
        lifecycle=test_lifecycle_writer(),
    )
    arguments = dict(
        run=successor,
        state=state,
        expected_thread_version=version,
        expected_current_run_id=source.id,
        expected_head_run_id=head,
        next_head_run_id=head,
    )

    async def abort(database, receipt):
        assert (
            await database.scalar(
                select(func.count())
                .select_from(RunEnvironmentMountRecord)
                .where(RunEnvironmentMountRecord.run_id == successor.id)
            )
            == 2
        )
        raise RuntimeError("injected acceptance rollback")

    with pytest.raises(RuntimeError, match="injected acceptance rollback"):
        await acceptance.advance_thread(**arguments, transaction_hook=abort)
    async with short_session(sessions) as database:
        assert await database.get(RunRecord, successor.id) is None
        assert (
            await database.scalar(
                select(func.count())
                .select_from(RunEnvironmentMountRecord)
                .where(RunEnvironmentMountRecord.run_id == successor.id)
            )
            == 0
        )
    receipt = await acceptance.advance_thread(**arguments)
    assert await acceptance.advance_thread(**arguments) == receipt
    async with short_session(sessions) as database:
        rows = (
            await database.scalars(
                select(RunEnvironmentMountRecord)
                .where(RunEnvironmentMountRecord.run_id == successor.id)
                .order_by(RunEnvironmentMountRecord.created_at)
            )
        ).all()
        assert [row.name for row in rows] == ["first", "second"]
        for index, row in enumerate(rows):
            original = await database.get(RunEnvironmentMountRecord, (source.id, row.name))
            assert row.environment_id == environment.id
            assert assume_utc(row.created_at) == NOW + timedelta(microseconds=index)
            assert (row.principal_type, row.principal_id) == (original.principal_type, original.principal_id)
            assert row.use_started_at is None and row.application_status == "pending"
            assert row.applied_attempt_id is None and row.applied_attempt_fence is None
            assert row.observed_at is None and row.error is None
        assert (await database.get(RunEnvironmentMountRecord, (source.id, "first"))).use_started_at is not None


@pytest.mark.parametrize("lineage", [RunLineageKind.root, RunLineageKind.continue_, RunLineageKind.fork])
async def test_new_execution_does_not_inherit_additions(
    interaction_sessions,
    interaction_object_store,
    client_environment,
    lineage,
):
    _, _, environment = client_environment
    _, source, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as database:
        database.add(accepted_mount(source.id, environment.id))
        await database.flush()
        candidate = source.model_copy(
            update={"id": "run_8888888888888888", "parent_run_id": source.id, "lineage_kind": lineage}
        )
        await inherit_run_mounts(database, run=candidate, workspace_id=WORKSPACE_ID)
        assert await database.scalar(select(func.count()).select_from(RunEnvironmentMountRecord)) == 1
