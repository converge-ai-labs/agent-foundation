"""Run outcomes release aggregate use before the maintenance loop applies idle policy."""

from datetime import timedelta

import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_harness import SafeFailure
from a13n_service.agents.models import AgentRecord
from a13n_service.environments.domain import CreateProviderRequest, CreateTemplateRequest
from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.interactions.attempts import AttemptExecutionService, AttemptPreparationAccepted
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc

from tests.environments.test_lifecycle import Target
from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import AGENT_ID, NOW, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _completed_state, _waiting_state, _worker
from .test_environment_capacity import sibling_run
from .test_environment_runtime import recipe

pytestmark = pytest.mark.anyio


@pytest.fixture
async def retained_environment(interaction_sessions, tmp_path, monkeypatch):
    service, _, lifecycle = await recipe(interaction_sessions, tmp_path, "on_run")
    service.catalog = lifecycle.catalog = build_environment_provider_catalog(builtin_keys=("a13n.docker",))
    provider = await service.create_provider(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.docker", name="Docker")
    )
    template = await service.create_template(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="retention-template",
        request=CreateTemplateRequest(
            name="Retained",
            provider_id=provider.id,
            configuration={},
            retention={"idle": {"stop_after": 60, "delete_after": 120}},
        ),
    )
    async with transaction(interaction_sessions) as session:
        (await session.get(AgentRecord, AGENT_ID)).default_environment_template_id = template.id
    events = []

    async def construct(operation):
        return Target(operation.state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    return lifecycle, events


async def cancel(sessions, objects, run, now, *, transaction_hook=None):
    async with short_session(sessions) as session:
        current = await session.get(RunRecord, run.id)
        thread = await session.get(ThreadRecord, run.thread_id)
        versions = current.version, thread.version
    await RunOutcomeService(
        sessions,
        RunPayloadStore(objects),
        lifecycle=test_lifecycle_writer(),
        clock=lambda: now,
    ).cancel(
        organization_id=run.organization_id,
        run_id=run.id,
        expected_run_version=versions[0],
        expected_thread_version=versions[1],
        failure=SafeFailure(code="cancelled", message="User cancelled"),
        transaction_hook=transaction_hook,
    )


@pytest.mark.parametrize(
    "outcome", ["cancelled", "completed", "waiting", "failed", "preparation_budget", "claim_budget"]
)
async def test_last_user_outcome_starts_idle_clock_and_automatic_cleanup(
    interaction_sessions, interaction_object_store, retained_environment, outcome
):
    lifecycle, events = retained_environment
    states, run, state = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        lifecycle=test_lifecycle_writer(),
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    await environment.close()
    ended_at = NOW + timedelta(seconds=3)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: ended_at, lifecycle=test_lifecycle_writer())
    if outcome == "cancelled":
        await cancel(interaction_sessions, interaction_object_store, run, ended_at)
    elif outcome in {"completed", "waiting"}:
        authority = _authority(claim)
        preparation = await execution.commit_preparation_success(authority)
        assert isinstance(preparation, AttemptPreparationAccepted)
        entered = await execution.enter_harness(authority, preparation=preparation, harness_run_id="retention-run")
        authority = _authority(claim, run_version=entered.run_version, attempt_version=entered.attempt_version)
        candidate = (_completed_state if outcome == "completed" else _waiting_state)(
            state, claim.attempt.id, claim.attempt.fence
        )
        stored = await execution.publish_checkpoint(
            authority, states, await states.read(run.organization_id, run.id), candidate
        )
        await RunOutcomeService(
            interaction_sessions,
            RunPayloadStore(interaction_object_store),
            clock=lambda: ended_at,
            lifecycle=test_lifecycle_writer(),
        ).commit_state_outcome(authority, stored, expected_thread_version=1)
    elif outcome == "failed":
        await execution.fail(_authority(claim), SafeFailure(code="failed", message="Failed"), retryable=False)
    else:
        async with transaction(interaction_sessions) as session:
            (await session.get(RunRecord, run.id)).recovery_deadline_at = ended_at
        if outcome == "preparation_budget":
            await execution.commit_preparation_success(_authority(claim))
        else:
            ended_at = NOW + timedelta(seconds=32)
            await AttemptScheduler(
                interaction_sessions,
                clock=lambda: ended_at,
                lifecycle=test_lifecycle_writer(),
            ).claim(run.id, _worker())
    async with short_session(interaction_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.environment_id)
        assert row.retention_condition == "idle"
        assert assume_utc(row.condition_since) == assume_utc(row.next_maintenance_at) == ended_at
    loop = EnvironmentMaintenanceLoop(lifecycle)
    for elapsed, status in ((59, "running"), (60, "stopped"), (119, "stopped"), (120, "deleted")):
        lifecycle.clock = lambda elapsed=elapsed: ended_at + timedelta(seconds=elapsed)
        await loop.run_once()
        async with short_session(interaction_sessions) as session:
            row = await session.get(EnvironmentRecord, environment.environment_id)
            assert row.status == status
            assert assume_utc(row.condition_since) == ended_at
    assert events.count("stop") == events.count("delete") == 1


async def test_failed_outcome_transaction_preserves_active_use(
    interaction_sessions, interaction_object_store, retained_environment
):
    lifecycle, events = retained_environment
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    environment = await prepare_run_environment(lifecycle, _authority(claim))
    await environment.close()

    async def fail_commit(session):
        row = await session.get(EnvironmentRecord, environment.environment_id)
        assert row.retention_condition == "idle"
        raise RuntimeError("Outcome rejected")

    with pytest.raises(RuntimeError, match="Outcome rejected"):
        await cancel(
            interaction_sessions,
            interaction_object_store,
            run,
            NOW + timedelta(seconds=3),
            transaction_hook=fail_commit,
        )
    lifecycle.clock = lambda: NOW + timedelta(hours=1)
    await EnvironmentMaintenanceLoop(lifecycle).run_once()
    async with short_session(interaction_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.environment_id)
        assert row.status == "running" and row.retention_condition == "active"
        assert row.next_maintenance_at is None
        assert (await session.get(RunRecord, run.id)).status == "running"
    assert "stop" not in events and "delete" not in events


async def test_shared_use_and_retry_backoff_do_not_start_idle_cleanup(
    interaction_sessions, interaction_object_store, retained_environment
):
    lifecycle, events = retained_environment
    _, first, _ = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        lifecycle=test_lifecycle_writer(),
    )
    first_claim = await scheduler.claim(first.id, _worker())
    first_env = await prepare_run_environment(lifecycle, _authority(first_claim))
    second = await sibling_run(interaction_sessions, first, first_env.environment_id)
    second_claim = await scheduler.claim(second.id, _worker())
    second_env = await prepare_run_environment(lifecycle, _authority(second_claim))
    await first_env.close()
    await second_env.close()
    await cancel(interaction_sessions, interaction_object_store, first, NOW + timedelta(seconds=3))
    await AttemptExecutionService(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=4),
        lifecycle=test_lifecycle_writer(),
    ).fail(
        _authority(second_claim),
        SafeFailure(code="transient", message="Try again"),
        retryable=True,
        retry_after=timedelta(seconds=300),
    )
    lifecycle.clock = lambda: NOW + timedelta(seconds=150)
    await EnvironmentMaintenanceLoop(lifecycle).run_once()
    async with short_session(interaction_sessions) as session:
        row = await session.get(EnvironmentRecord, first_env.environment_id)
        assert row.retention_condition == "active" and row.status == "running"
        assert row.next_maintenance_at is None
    assert "stop" not in events and "delete" not in events
    await cancel(interaction_sessions, interaction_object_store, second, NOW + timedelta(seconds=151))
    async with short_session(interaction_sessions) as session:
        row = await session.get(EnvironmentRecord, first_env.environment_id)
        assert row.retention_condition == "idle"
        assert assume_utc(row.condition_since) == NOW + timedelta(seconds=151)
