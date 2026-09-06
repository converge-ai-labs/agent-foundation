from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import SafeFailure
from a13n_service.agents.domain import (
    ChildEnvironmentPolicy,
    DelegationContextPolicy,
    ResolvedAgentModel,
    ResolvedSubagentEdge,
    canonical_digest,
)
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.attempt_executor import RunAttemptExecutor
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptExecutionService, AttemptPreparationError
from a13n_service.interactions.harness_runtime import HarnessDriver
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunObjectIntegrityError, RunStateStore
from a13n_service.interactions.preparation import AttemptDependencyLoader
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import ObjectNotFound, ObjectStoreUnavailable, short_session, transaction
from a13n_service.temporal import assume_utc
from anyio import Event, create_task_group, fail_after, sleep_forever

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    MODEL_ID,
    MODEL_KEY,
    NOW,
    ORGANIZATION_ID,
    USER_ID,
    WORKSPACE_ID,
    agent_config,
    effective_agent_config,
)
from .test_attempt_execution import _accept_root, _authority, _worker

pytestmark = pytest.mark.anyio


async def _authorize_fixture(sessions):
    async with transaction(sessions) as database:
        database.add(
            UserRecord(
                id=USER_ID,
                email="worker@example.com",
                normalized_email="worker@example.com",
                name="Worker test",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        database.add_all(
            [
                RoleBindingRecord(
                    id="rb_org1234567890abcd",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="organization",
                    resource_id=ORGANIZATION_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_ws1234567890abcde",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="builder",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
        revision = await database.get(AgentRevisionRecord, AGENT_REVISION_ID)
        model = agent_config().model
        revision.resolved_model = ResolvedAgentModel(
            model_id=MODEL_ID, model_key=MODEL_KEY, settings=model.settings, characteristics=model.characteristics
        ).model_dump(mode="json")


async def _claimed(sessions, objects):
    await _authorize_fixture(sessions)
    states, run, _ = await _accept_root(sessions, objects)
    claim = await AttemptScheduler(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    return states, run, _authority(claim)


async def test_dependency_loading_uses_frozen_revision_and_survives_heartbeat(
    interaction_sessions, interaction_object_store
):
    states, run, authority = await _claimed(interaction_sessions, interaction_object_store)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))
    loader = AttemptDependencyLoader(interaction_sessions, clock=lambda: NOW)
    loaded = await loader.load(authority, await states.read(ORGANIZATION_ID, run.id))
    assert loaded.run.id == run.id
    assert loaded.workspace_id == WORKSPACE_ID
    assert loaded.root_revision.id == AGENT_REVISION_ID
    assert not loaded.child_revisions
    with pytest.raises(TypeError):
        loaded.child_revisions["changed"] = loaded.root_revision
    with pytest.raises(AttemptAuthorityError):
        await execution.validate(authority)
    with pytest.raises(AttemptAuthorityError):
        await loader.load(replace(authority, lease_token="wrong"), await states.read(ORGANIZATION_ID, run.id))


@pytest.mark.parametrize("revocation", ["principal", "membership", "agent_disabled", "agent_archived"])
async def test_dependency_loading_rechecks_current_authority(
    interaction_sessions, interaction_object_store, revocation
):
    states, run, authority = await _claimed(interaction_sessions, interaction_object_store)
    loader = AttemptDependencyLoader(interaction_sessions, clock=lambda: NOW)
    state = await states.read(ORGANIZATION_ID, run.id)
    await loader.load(authority, state)
    async with transaction(interaction_sessions) as database:
        if revocation == "principal":
            (await database.get(UserRecord, USER_ID)).status = "disabled"
        elif revocation == "membership":
            await database.delete(await database.get(RoleBindingRecord, "rb_org1234567890abcd"))
        elif revocation == "agent_disabled":
            (await database.get(AgentRecord, AGENT_ID)).enabled = False
        else:
            (await database.get(AgentRecord, AGENT_ID)).archived_at = NOW
    with pytest.raises(AttemptPreparationError) as captured:
        await loader.load(authority, state)
    assert not captured.value.retryable
    assert captured.value.failure.code in {"run_authorization_denied", "agent_revision_unavailable"}


async def test_dependency_loading_rejects_changed_configuration_body(interaction_sessions, interaction_object_store):
    states, run, authority = await _claimed(interaction_sessions, interaction_object_store)
    state = await states.read(ORGANIZATION_ID, run.id)
    changed = replace(
        state,
        envelope=state.envelope.model_copy(
            update={
                "effective_agent_config": state.envelope.effective_agent_config.model_copy(
                    update={"instructions": "Changed without the accepted digest"}
                )
            }
        ),
    )
    with pytest.raises(AttemptPreparationError) as captured:
        await AttemptDependencyLoader(interaction_sessions, clock=lambda: NOW).load(authority, changed)
    assert captured.value.failure.code == "run_state_identity_mismatch"


@pytest.mark.parametrize("child_problem", [None, "disabled", "mode", "missing_revision"])
async def test_dependency_loading_rechecks_transitive_frozen_children(
    interaction_sessions, interaction_object_store, child_problem
):
    await _authorize_fixture(interaction_sessions)
    edges = tuple(
        ResolvedSubagentEdge(
            name=f"child-{number}",
            child_agent_id=f"agt_{number:016d}",
            child_agent_revision_id=f"agtr_{number:016d}",
            context=DelegationContextPolicy(),
            environment=ChildEnvironmentPolicy(),
        )
        for number in (2, 3)
    )
    async with transaction(interaction_sessions) as database:
        agent = await database.get(AgentRecord, AGENT_ID)
        revision = await database.get(AgentRevisionRecord, AGENT_REVISION_ID)
        for index, edge in enumerate(edges):
            agent_fields = {
                column.key: deepcopy(getattr(agent, column.key)) for column in AgentRecord.__table__.columns
            }
            agent_fields.update(
                id=edge.child_agent_id,
                current_revision_id=edge.child_agent_revision_id,
                name=edge.name,
                normalized_name=edge.name,
            )
            revision_fields = {
                column.key: deepcopy(getattr(revision, column.key)) for column in AgentRevisionRecord.__table__.columns
            }
            revision_fields.update(
                id=edge.child_agent_revision_id,
                agent_id=edge.child_agent_id,
                resolved_subagents=[edges[1].model_dump(mode="json")] if index == 0 else [],
            )
            database.add(AgentRecord(**agent_fields))
            await database.flush()
            database.add(AgentRevisionRecord(**revision_fields))
        await database.flush()
        if child_problem == "disabled":
            (await database.get(AgentRecord, edges[1].child_agent_id)).enabled = False
        elif child_problem == "mode":
            (await database.get(AgentRevisionRecord, edges[1].child_agent_revision_id)).plugin_runtime_mode = "runner"
        elif child_problem == "missing_revision":
            revision = await database.get(AgentRevisionRecord, edges[0].child_agent_revision_id)
            revision.resolved_subagents = [
                edges[1].model_copy(update={"child_agent_revision_id": "agtr_9999999999999999"}).model_dump(mode="json")
            ]
    config = effective_agent_config().model_copy(update={"resolved_subagents": (edges[0],)})
    config = config.model_copy(
        update={
            "content_digest": canonical_digest(
                config.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
            )
        }
    )
    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store, effective_config=config)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    loader = AttemptDependencyLoader(interaction_sessions, clock=lambda: NOW)
    state = await states.read(ORGANIZATION_ID, run.id)
    if child_problem is None:
        loaded = await loader.load(_authority(claim), state)
        assert set(loaded.child_revisions) == {edge.child_agent_revision_id for edge in edges}
    else:
        with pytest.raises(AttemptPreparationError) as captured:
            await loader.load(_authority(claim), state)
        assert not captured.value.retryable
        assert captured.value.failure.code == (
            "subagent_runtime_mode_mismatch" if child_problem == "mode" else "agent_revision_unavailable"
        )


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (ObjectNotFound("absent"), "run_state_invalid", False),
        (RunObjectIntegrityError("invalid digest"), "run_state_invalid", False),
        (ObjectStoreUnavailable("private backend detail"), "run_state_unavailable", True),
        (TimeoutError(), "run_state_unavailable", True),
    ],
)
@pytest.mark.parametrize("budget", [1, 3])
async def test_state_load_failure_settles_before_harness_entry(
    interaction_sessions, interaction_object_store, error, code, retryable, budget
):
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store, max_recovery_attempts=budget)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    authority = _authority(claim)
    states = Mock(spec=RunStateStore)
    states.read = AsyncMock(side_effect=error)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    inbox = DatabaseThreadInboxReconciler(interaction_sessions, AsyncMock(), clock=lambda: NOW)
    control = RunAttemptControl(context=authority, execution=execution, states=states, inbox=inbox)
    driver = Mock(spec=HarnessDriver)
    preparer, wakeups, cleanup, capacity = AsyncMock(), AsyncMock(), AsyncMock(), Mock()
    executor = RunAttemptExecutor(
        context=authority,
        control=control,
        driver=driver,
        preparer=preparer,
        wakeups=wakeups,
        adapter=Mock(),
        committer=Mock(),
        cleanup=cleanup,
        capacity_slot=capacity,
        environments=Mock(),
    )

    result = await executor.run()

    will_retry = retryable and budget > 1
    assert result.disposition == ("retrying" if will_retry else "failed")
    driver.run.assert_not_awaited()
    preparer.prepare.assert_not_awaited()
    wakeups.receive.assert_not_awaited()
    cleanup.close.assert_awaited_once()
    capacity.release.assert_called_once()
    async with short_session(interaction_sessions) as database:
        record = await database.get(RunRecord, run.id)
        attempt = await database.get(RunAttemptRecord, authority.run_attempt_id)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert record.current_run_attempt_id is None
        assert record.status == ("running" if will_retry else "failed")
        assert (record.sealed_at is None) == will_retry
        if will_retry:
            assert assume_utc(record.available_at) == NOW + timedelta(seconds=1)
        assert thread.version == (1 if will_retry else 2)
        assert thread.current_run_id == run.id
        assert attempt.status == "failed"
        assert attempt.started_at is None and attempt.harness_run_id is None
        assert attempt.failure_json["code"] == code
        assert "private backend detail" not in str(attempt.failure_json)


async def test_slow_state_read_does_not_block_lease_renewal(interaction_sessions, interaction_object_store):
    states, run, authority = await _claimed(interaction_sessions, interaction_object_store)
    reading, renewed = Event(), Event()
    read = states.read
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())

    async def slow_read(*args, **kwargs):
        reading.set()
        await renewed.wait()
        return await read(*args, **kwargs)

    states.read = slow_read
    control = RunAttemptControl(context=authority, execution=execution, states=states, inbox=Mock())
    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(control.load_state)
            await reading.wait()
            await control.renew_lease()
            renewed.set()
    assert control.current_state.envelope.run_id == run.id
    assert control.current_context.expected_attempt_version == authority.expected_attempt_version + 1


@pytest.mark.parametrize("classified", [True, False, "timeout"])
async def test_dependency_failure_never_enters_harness_or_hides_programming_errors(
    interaction_sessions, interaction_object_store, classified
):
    states, _, authority = await _claimed(interaction_sessions, interaction_object_store)
    if classified == "timeout":
        authority = replace(authority, preparation_timeout=timedelta(milliseconds=10))
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    control = RunAttemptControl(
        context=authority,
        execution=execution,
        states=states,
        inbox=DatabaseThreadInboxReconciler(interaction_sessions, AsyncMock(), clock=lambda: NOW),
    )
    failure = (
        AttemptPreparationError(
            SafeFailure(code="run_authorization_denied", message="Principal is no longer authorized."), retryable=False
        )
        if classified
        else RuntimeError("unexpected programming error")
    )
    preparer, cleanup, wakeups, capacity = AsyncMock(), AsyncMock(), AsyncMock(), Mock()

    async def stalled_preparation(context):
        await sleep_forever()

    preparer.prepare.side_effect = stalled_preparation if classified == "timeout" else failure
    wakeups.receive.side_effect = sleep_forever
    driver = Mock(spec=HarnessDriver)
    executor = RunAttemptExecutor(
        context=authority,
        control=control,
        driver=driver,
        preparer=preparer,
        wakeups=wakeups,
        adapter=Mock(),
        committer=Mock(),
        cleanup=cleanup,
        capacity_slot=capacity,
        environments=Mock(),
    )
    if classified:
        assert (await executor.run()).disposition == ("retrying" if classified == "timeout" else "failed")
    else:
        with pytest.raises(ExceptionGroup) as captured:
            await executor.run()
        assert captured.value.exceptions == (failure,)
    driver.run.assert_not_awaited()
    cleanup.close.assert_awaited_once()
    capacity.release.assert_called_once()
    async with short_session(interaction_sessions) as database:
        attempt = await database.get(RunAttemptRecord, authority.run_attempt_id)
        assert attempt.status == ("failed" if classified else "leased")
        assert attempt.harness_run_id is None


@pytest.mark.parametrize("operation", ["claim_writer", "checkpoint"])
async def test_state_publication_does_not_block_renewal(
    interaction_sessions, interaction_object_store, monkeypatch, operation
):
    states, _, authority = await _claimed(interaction_sessions, interaction_object_store)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    control = RunAttemptControl(context=authority, execution=execution, states=states, inbox=Mock())
    await control.load_state()
    publishing, renewed = Event(), Event()
    if operation == "checkpoint":
        await control.commit_preparation()
    method_name = "claim_writer" if operation == "claim_writer" else "replace"
    publish = getattr(states, method_name)

    async def slow_publish(*args, **kwargs):
        publishing.set()
        await renewed.wait()
        return await publish(*args, **kwargs)

    monkeypatch.setattr(states, method_name, slow_publish)

    async def run_publication():
        if operation == "claim_writer":
            await control.commit_preparation()
        else:
            envelope = control.current_state.envelope
            successor = envelope.model_copy(
                update={
                    "checkpoint_seq": envelope.checkpoint_seq + 1,
                    "checkpoint_kind": "progress",
                    "input_disposition": "applied",
                    "last_checkpoint_run_attempt_id": authority.run_attempt_id,
                    "last_checkpoint_fence": authority.fence,
                }
            )
            await control._publish(successor)

    version = control.current_context.expected_attempt_version
    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(run_publication)
            await publishing.wait()
            await control.renew_lease()
            renewed.set()
    assert control.current_context.expected_attempt_version > version
    await execution.validate(control.current_context)
    assert control.current_state.writer_fence == authority.fence
    async with short_session(interaction_sessions) as database:
        attempt = await database.get(RunAttemptRecord, authority.run_attempt_id)
        assert attempt.status == "leased"
        assert attempt.harness_run_id is None
