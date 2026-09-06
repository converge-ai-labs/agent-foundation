from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_environment_provider import EnvironmentProviderCatalog
from a13n_harness import AgentIdentityRef, AgentInstanceContext, HarnessBuilder
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.agents.domain import canonical_digest
from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.interactions.attempt_executor import RunAttemptExecutor
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.harness_results import StoredHarnessOutcomeAdapter
from a13n_service.interactions.harness_runtime import (
    HarnessCollaborators,
    HarnessDriver,
    HarnessInvocation,
    ImmediateHarnessInput,
)
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.preflight import OnDemandExecutionPreflight, RunnerExecutionPreflight
from a13n_service.interactions.preparation import AttemptDependencyLoader
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.terminal import DatabaseRunTerminalCommitter
from a13n_service.plugins.models import PluginRuntimeLockRecord
from a13n_service.plugins.on_demand import OnDemandPluginRuntimeDeclined, OnDemandPluginRuntimeFatalError
from a13n_service.plugins.runner_bootstrap import BootstrappedPluginRuntime
from a13n_service.plugins.runtime import PluginRuntimeLockStore, WorkerReleaseManifest, default_runtime_target
from a13n_service.storage import short_session, transaction
from anyio import Event, create_task_group, fail_after, sleep_forever
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import event, select

from tests.lifecycle_support import test_lifecycle_writer

from ..plugins.test_on_demand import _runtime
from .conftest import NOW, ORGANIZATION_ID, effective_agent_config
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_attempt_executor import _Projector
from .test_preparation import _authorize_fixture
from .test_worker import _Attempt, _loop

pytestmark = pytest.mark.anyio


async def _fixture(sessions, objects, tmp_path, *, mode="on_demand"):
    locks = PluginRuntimeLockStore(
        WorkerReleaseManifest("worker-v1", "harness-v1", default_runtime_target(), {}), clock=lambda: NOW
    )
    async with transaction(sessions) as database:
        lock = await locks.build_and_persist(database, mode=mode, plugins=())
    config = effective_agent_config().model_copy(update={"runtime_lock_digest": lock.digest})
    config = config.model_copy(
        update={
            "content_digest": canonical_digest(
                config.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
            )
        }
    )
    states, run, _ = await _accept_root(sessions, objects, effective_config=config)
    scheduler = AttemptScheduler(sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW)
    (candidate,) = await scheduler.discover(worker_build_id="build-1", handoff_preference_window=timedelta(seconds=30))
    runtime = await _runtime(tmp_path, objects)
    return locks, lock, runtime, scheduler, candidate, states, run


async def test_exact_runtime_materialization_finishes_outside_database_before_claim(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    locks, lock, runtime, _, candidate, _, _ = await _fixture(interaction_sessions, interaction_object_store, tmp_path)
    checked_out = set()
    engine = interaction_sessions.kw["bind"]

    def checkout(connection, record, proxy):
        checked_out.add(id(record))

    def checkin(connection, record):
        checked_out.discard(id(record))

    event.listen(engine.sync_engine, "checkout", checkout)
    event.listen(engine.sync_engine, "checkin", checkin)
    entered, proceed = Event(), Event()
    prepare = runtime.prepare_for_claim

    async def observe(selected):
        assert selected == lock
        assert not checked_out
        entered.set()
        await proceed.wait()
        return await prepare(selected)

    monkeypatch.setattr(runtime, "prepare_for_claim", observe)
    attempt = _Attempt()
    constructed = []

    def factory(claim, slot, catalog):
        assert claim.attempt.runtime_lock_digest == lock.digest
        assert isinstance(catalog, HarnessPluginFactoryCatalog)
        assert not tuple(catalog)
        assert not checked_out
        constructed.append(claim)
        return attempt

    preflight = OnDemandExecutionPreflight(interaction_sessions, locks, runtime, factory, timeout_seconds=5)
    scheduler, loop = _loop(interaction_sessions, preflight)
    try:
        with fail_after(5):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                await entered.wait()
                assert scheduler.claims == 0
                assert loop.active_count == 0
                async with short_session(interaction_sessions) as database:
                    assert await database.scalar(select(RunAttemptRecord.id)) is None
                proceed.set()
                await attempt.started.wait()
                await loop.drain()
                attempt.finish.set()
                await loop.wait_stopped()
        assert len(constructed) == 1
        assert constructed[0].attempt.run_id == candidate.run_id
        assert loop.active_count == 0
    finally:
        event.remove(engine.sync_engine, "checkout", checkout)
        event.remove(engine.sync_engine, "checkin", checkin)


@pytest.mark.parametrize("problem", ["missing", "invalid", "mode", "organization", "digest", "conflict", "timeout"])
async def test_declined_preflight_never_allocates_an_attempt(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, problem
):
    locks, lock, runtime, _, candidate, _, _ = await _fixture(
        interaction_sessions, interaction_object_store, tmp_path, mode="runner" if problem == "mode" else "on_demand"
    )
    if problem in {"missing", "invalid"}:
        async with transaction(interaction_sessions) as database:
            record = await database.get(PluginRuntimeLockRecord, lock.digest)
            if problem == "missing":
                await database.delete(record)
            else:
                record.manifest = {"malformed": True}
    elif problem == "organization":
        candidate = replace(candidate, organization_id="org_9999999999999999")
    elif problem == "digest":
        candidate = replace(candidate, runtime_lock_digest="f" * 64)
    prepare = AsyncMock(wraps=runtime.prepare_for_claim)
    monkeypatch.setattr(runtime, "prepare_for_claim", prepare)
    if problem == "conflict":
        prepare.side_effect = OnDemandPluginRuntimeDeclined("plugin_runtime_process_conflict")
    elif problem == "timeout":

        async def never_ready(lock):
            await sleep_forever()

        prepare.side_effect = never_ready
    factory = Mock()
    preflight = OnDemandExecutionPreflight(
        interaction_sessions, locks, runtime, factory, timeout_seconds=0.1 if problem == "timeout" else 5
    )
    assert await preflight.prepare(candidate) is None
    factory.assert_not_called()
    if problem not in {"conflict", "timeout"}:
        prepare.assert_not_awaited()
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(RunAttemptRecord.id)) is None
        record = await database.get(RunRecord, candidate.run_id)
        assert record.attempts_started == 0
        assert record.recovery_attempts_started == 0
        assert record.status == "accepted"


async def test_fatal_plugin_import_failure_escapes_preflight(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    locks, _, runtime, _, candidate, _, _ = await _fixture(interaction_sessions, interaction_object_store, tmp_path)
    fatal = OnDemandPluginRuntimeFatalError("plugin_factory_load_failed")
    monkeypatch.setattr(runtime, "prepare_for_claim", AsyncMock(side_effect=fatal))
    factory = Mock()
    with pytest.raises(OnDemandPluginRuntimeFatalError) as captured:
        await OnDemandExecutionPreflight(interaction_sessions, locks, runtime, factory, timeout_seconds=5).prepare(
            candidate
        )
    assert captured.value is fatal
    factory.assert_not_called()


@pytest.mark.parametrize("mode", ["on_demand", "runner"])
async def test_launcher_cannot_be_reused_for_another_run_or_runtime(
    interaction_sessions, interaction_object_store, tmp_path, mode
):
    locks, lock, runtime, scheduler, candidate, _, _ = await _fixture(
        interaction_sessions, interaction_object_store, tmp_path, mode=mode
    )
    factory = Mock(return_value=_Attempt())
    preflight = (
        RunnerExecutionPreflight(BootstrappedPluginRuntime(lock, HarnessPluginFactoryCatalog(())), factory)
        if mode == "runner"
        else OnDemandExecutionPreflight(interaction_sessions, locks, runtime, factory, timeout_seconds=5)
    )
    launcher = await preflight.prepare(candidate)
    assert launcher is not None
    claim = await scheduler.claim(candidate.run_id, replace(_worker(), runtime_lock_digest=lock.digest))
    assert isinstance(claim, ClaimedAttempt)
    for fields in [
        {"organization_id": "org_9999999999999999"},
        {"run_id": "run_9999999999999999"},
        {"runtime_lock_digest": "f" * 64},
    ]:
        with pytest.raises(ValueError, match="does not match"):
            launcher.create(replace(claim, attempt=claim.attempt.model_copy(update=fields)), Mock())
    factory.assert_not_called()
    assert launcher.create(claim, Mock()) is factory.return_value
    if mode == "runner":
        assert await preflight.prepare(replace(candidate, runtime_lock_digest="f" * 64)) is None


async def test_preflight_claim_executor_harness_and_durable_completion(
    interaction_sessions, interaction_object_store, tmp_path
):
    locks, _, runtime, _, _, states, run = await _fixture(interaction_sessions, interaction_object_store, tmp_path)
    await _authorize_fixture(interaction_sessions)
    execution = AttemptExecutionService(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW)
    dependencies = AttemptDependencyLoader(interaction_sessions, clock=lambda: NOW)
    payloads = RunPayloadStore(interaction_object_store)
    outcomes = RunOutcomeService(interaction_sessions, payloads, lifecycle=test_lifecycle_writer(), clock=lambda: NOW)
    environments = EnvironmentLifecycle(interaction_sessions, EnvironmentProviderCatalog(), Mock(), tmp_path)
    projector = _Projector()
    cleanups = []
    model_calls = []

    async def model(messages, info):
        model_calls.append(messages)
        yield "completed by the claimed executor"

    def factory(claim, capacity, catalog):
        context = _authority(claim)
        control = RunAttemptControl(
            context=context,
            execution=execution,
            states=states,
            inbox=DatabaseThreadInboxReconciler(interaction_sessions, AsyncMock(), clock=lambda: NOW),
        )
        driver = HarnessDriver(HarnessBuilder(instrumentation=None), control=control, projector=projector)

        async def prepare(authority):
            loaded = await dependencies.load(authority, control.current_state)
            definition = AgentReconstructor(catalog).reconstruct(
                agent_id=loaded.run.agent_id,
                agent_revision_id=loaded.run.agent_revision_id,
                effective_config=control.current_state.envelope.effective_agent_config,
                child_revisions=loaded.child_revisions,
            )

            async def resolve_model(resolution_context, model_id):
                assert (
                    model_id == control.current_state.envelope.effective_agent_config.resolved_model.execution.model_id
                )
                return FunctionModel(stream_function=model)

            return HarnessInvocation(
                definition=definition,
                input=ImmediateHarnessInput("hello"),
                collaborators=HarnessCollaborators(
                    model_resolver=resolve_model,
                    instance=AgentInstanceContext(
                        identity=AgentIdentityRef(
                            issuer="foundation", subject=loaded.run.authority_principal.principal_id
                        ),
                        agent_instance_id="instance-1",
                        actor="user:test-user",
                        host_refs={"session_id": loaded.run.session_id},
                    ),
                ),
            )

        async def cleanup(authority, control, driver):
            cleanups.append(authority.run_attempt_id)
            await loop.drain()

        return RunAttemptExecutor(
            context=context,
            control=control,
            driver=driver,
            preparer=Mock(prepare=prepare),
            wakeups=Mock(receive=sleep_forever),
            adapter=StoredHarnessOutcomeAdapter(
                organization_id=ORGANIZATION_ID,
                run_id=run.id,
                payloads=payloads,
                max_output_bytes=4096,
                inline_output_bytes=4096,
            ),
            committer=DatabaseRunTerminalCommitter(interaction_sessions, outcomes, execution, clock=lambda: NOW),
            cleanup=Mock(close=cleanup),
            capacity_slot=capacity,
            environments=environments,
        )

    preflight = OnDemandExecutionPreflight(interaction_sessions, locks, runtime, factory, timeout_seconds=5)
    scheduler, loop = _loop(interaction_sessions, preflight)
    with fail_after(5):
        await loop.run()
    assert scheduler.claims == 1
    assert loop.active_count == 0
    assert len(cleanups) == len(model_calls) == 1
    assert projector.events
    async with short_session(interaction_sessions) as database:
        record = await database.get(RunRecord, run.id)
        attempt = await database.get(RunAttemptRecord, cleanups[0])
        assert record.status == "completed"
        assert record.current_run_attempt_id is None
        assert record.sealed_at is not None
        assert attempt.status == "succeeded"
        assert attempt.harness_run_id is not None
    state = await states.read(ORGANIZATION_ID, run.id)
    assert state.envelope.outcome_candidate.output == "completed by the claimed executor"
    assert state.envelope.writer_fence == state.envelope.last_checkpoint_fence == 1
    assert state.envelope.input_disposition == "applied"
