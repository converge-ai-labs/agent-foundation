from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.agents.domain import canonical_digest
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, WorkerClaim
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.plugins.models import PluginRuntimeLockRecord
from a13n_service.plugins.runtime import PluginRuntimeLock, default_runtime_target, installed_harness_version
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, fail_after, sleep
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.messages import TextContent as NativeTextContent
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID, effective_agent_config
from .worker_helpers import worker_runtime

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("recover_candidate", [False, True])
@pytest.mark.parametrize("late_input", [False, True])
async def test_worker_claims_and_executes_an_accepted_run_in_process(
    interaction_sessions,
    interaction_object_store,
    tmp_path,
    monkeypatch,
    recover_candidate,
    late_input,
):
    lock = PluginRuntimeLock(
        mode="on_demand",
        runtime_target=default_runtime_target(),
        worker_release="test",
        harness_version=installed_harness_version(),
        digest="0" * 64,
    )
    lock = lock.model_copy(update={"digest": lock.computed_digest()})
    config = effective_agent_config().model_copy(update={"runtime_lock_digest": lock.digest})
    config = config.model_copy(
        update={
            "content_digest": canonical_digest(
                config.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
            )
        }
    )
    monkeypatch.setattr(acceptance, "effective_agent_config", lambda: config)
    states, run, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as session:
        session.add(
            PluginRuntimeLockRecord(
                digest=lock.digest,
                schema_version="1",
                mode="on_demand",
                manifest=lock.model_dump(mode="json"),
                created_at=NOW,
            )
        )
        session.add(
            UserRecord(
                id=USER_ID,
                email="worker@example.com",
                normalized_email="worker@example.com",
                name="Worker User",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        for kind, identifier, role in (
            ("organization", ORGANIZATION_ID, "member"),
            ("workspace", WORKSPACE_ID, "admin"),
        ):
            session.add(
                RoleBindingRecord(
                    id=f"rb_worker_{kind}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID if kind == "workspace" else None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type=kind,
                    resource_id=identifier,
                    role_key=role,
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    if recover_candidate:
        if not late_input:
            monkeypatch.setattr(
                "a13n_service.interactions.worker_preparation.WorkerAttemptPreparer.open_runtime",
                AsyncMock(side_effect=AssertionError("Outcome adoption must not reconstruct a Harness invocation")),
            )
            monkeypatch.setattr(
                "a13n_service.interactions.worker_preparation.prepare_run_environment",
                AsyncMock(side_effect=AssertionError("Outcome adoption must not prepare Environment use")),
            )
        claim = await AttemptScheduler(
            interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).claim(
            run.id,
            WorkerClaim(
                organization_id=ORGANIZATION_ID,
                worker_id="prior",
                worker_build_id="test",
                runtime_lock_digest=lock.digest,
                lease_duration=timedelta(seconds=30),
                handoff_preference_window=timedelta(seconds=30),
            ),
        )
        assert isinstance(claim, ClaimedAttempt)
        context = acceptance._authority(claim)
        execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
        decision = await execution.commit_preparation_success(context)
        await execution.enter_harness(context, preparation=decision, harness_run_id="prior-harness-run")
        context = acceptance._authority(
            claim,
        )
        initial = await states.read(ORGANIZATION_ID, run.id)
        await execution.publish_checkpoint(
            context,
            states,
            initial,
            acceptance._completed_state(initial.envelope, claim.attempt.id, claim.attempt.attempt_number),
        )
    injected = False
    commit = RunOutcomeService.commit_verified_state_outcome

    async def commit_with_late_input(service, authority, verified, **kwargs):
        nonlocal injected
        if late_input and not injected:
            injected = True
            await ThreadInboxStore(interaction_sessions).append_steer(
                organization_id=run.organization_id,
                run_id=run.id,
                input=AcceptedAgentInput(schema_version="1", content=(TextContent(text="new direction"),)),
                entry_id="inb_9999999999999999",
            )
        return await commit(service, authority, verified, **kwargs)

    monkeypatch.setattr(RunOutcomeService, "commit_verified_state_outcome", commit_with_late_input)
    requests = []

    async def model(messages, info):
        requests.append(messages)
        yield "worker completed"

    model_factory = Mock(spec=NativeModelFactory)
    model_factory.build.return_value = FunctionModel(stream_function=model)
    settings = Settings(_env_file=None, build_version="test", worker_concurrency=1, worker_poll_interval_seconds=0.02)
    async with worker_runtime(
        interaction_sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=settings,
        lock=lock,
        model_factory=model_factory,
    ) as (runtime, _shared, preflight):
        loop = runtime.execution_loop
        assert loop is not None
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as session:
                        row = await session.get(RunRecord, run.id)
                        assert row is not None
                        if row.status in {"completed", "failed"}:
                            assert row.status == "completed", row.failure_json
                            assert row.output_json == (
                                {"answer": 42} if recover_candidate and not late_input else "worker completed"
                            )
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
        async with short_session(interaction_sessions) as session:
            attempt = await session.scalar(
                select(RunAttemptRecord)
                .where(RunAttemptRecord.run_id == run.id)
                .order_by(RunAttemptRecord.attempt_number.desc())
            )
            assert attempt.status == "succeeded"
            assert (attempt.harness_run_id is None) is (recover_candidate and not late_input)
        checkpoint = await states.read(ORGANIZATION_ID, run.id)
        assert checkpoint.envelope.checkpoint_kind == "completed"
        assert checkpoint.envelope.initial_input_applied
        assert checkpoint.writer_fence == (2 if recover_candidate or late_input else 1)
        assert len(requests) == (int(not recover_candidate) + int(late_input))
        if late_input:
            async with short_session(interaction_sessions) as session:
                entry = await session.get(ThreadInboxRecord, "inb_9999999999999999")
                assert entry.status == "consumed"
                attempts = tuple(
                    (
                        await session.scalars(
                            select(RunAttemptRecord)
                            .where(RunAttemptRecord.run_id == run.id)
                            .order_by(RunAttemptRecord.attempt_number)
                        )
                    ).all()
                )
                assert attempts[-1].start_reason == ("lease_expired" if recover_candidate else "pending_input")
                assert len(attempts) == 2
            assert [r.inbox_entry_id for r in checkpoint.envelope.host.inbox_receipts] == [entry.id]
            prompts = [
                item.content if isinstance(item, NativeTextContent) else item
                for message in requests[-1]
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
                for item in ((part.content,) if isinstance(part.content, str) else part.content)
            ]
            assert prompts.count("new direction") == 1
            assert prompts.count("hello") == int(not recover_candidate)
        assert preflight.await_count == (2 if late_input and not recover_candidate else 1)
        assert all(call.args == (lock,) for call in preflight.await_args_list)


@pytest.mark.parametrize("recover_candidate", [False, True])
async def test_postgresql_worker_continues_late_input_in_same_run(
    postgres_interaction_sessions, interaction_object_store, tmp_path, monkeypatch, recover_candidate
):
    await test_worker_claims_and_executes_an_accepted_run_in_process(
        postgres_interaction_sessions, interaction_object_store, tmp_path, monkeypatch, recover_candidate, True
    )
