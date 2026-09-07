from contextlib import AsyncExitStack
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_environment_provider import EnvironmentProviderCatalog
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.agents.domain import canonical_digest
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, WorkerClaim
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.plugins.models import PluginRuntimeLockRecord
from a13n_service.plugins.on_demand import OnDemandPluginRuntime, PreparedOnDemandPluginRuntime
from a13n_service.plugins.runtime import PluginRuntimeLock, default_runtime_target, installed_harness_version
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.process.worker import build_worker_runtime
from a13n_service.secrets import SecretProtector
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from a13n_service.storage.runtime import StorageResources
from anyio import CapacityLimiter, create_task_group, fail_after, sleep
from fakeredis.aioredis import FakeRedis
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID, effective_agent_config

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("recover_candidate", [False, True])
async def test_worker_claims_and_executes_an_accepted_run_in_process(
    interaction_sessions,
    interaction_object_store,
    tmp_path,
    monkeypatch,
    recover_candidate,
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
        monkeypatch.setattr(
            "a13n_service.interactions.worker_preparation.WorkerAttemptPreparer.prepare",
            AsyncMock(side_effect=AssertionError("Outcome adoption must not reconstruct a Harness invocation")),
        )
        monkeypatch.setattr(
            "a13n_service.interactions.attempt_executor.prepare_run_environment",
            AsyncMock(side_effect=AssertionError("Outcome adoption must not prepare Environment use")),
        )
        claim = await AttemptScheduler(
            interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).claim(
            run.id,
            WorkerClaim(
                organization_id=ORGANIZATION_ID,
                worker_id="prior",
                worker_generation="prior-generation",
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
        entered = await execution.enter_harness(context, preparation=decision, harness_run_id="prior-harness-run")
        context = acceptance._authority(claim, run_version=entered.run_version, attempt_version=entered.attempt_version)
        initial = await states.read(ORGANIZATION_ID, run.id)
        await execution.publish_checkpoint(
            context,
            states,
            initial,
            acceptance._completed_state(initial.envelope, claim.attempt.id, claim.attempt.fence),
        )
    preflight = AsyncMock(return_value=PreparedOnDemandPluginRuntime(lock.digest, HarnessPluginFactoryCatalog(())))
    monkeypatch.setattr(OnDemandPluginRuntime, "prepare_for_claim", preflight)
    monkeypatch.setattr(LiveProviderResolver, "resolve", AsyncMock(return_value=Mock()))
    requests = []

    async def model(messages, info):
        requests.append(messages)
        yield "worker completed"

    model_factory = Mock(spec=NativeModelFactory)
    model_factory.build.return_value = FunctionModel(stream_function=model)
    resources = Mock(spec=ExecutionResources)
    resources.native_model_factory = model_factory
    resources.plugin_objects = Mock()
    resources.skill_package_store = Mock()
    resources.model_provider_registry = Mock()
    resources.model_endpoint_policy = Mock()
    resources.model_http_client = Mock()
    settings = Settings(_env_file=None, build_version="test", worker_concurrency=1, worker_poll_interval_seconds=0.02)
    async with FakeRedis() as redis, AsyncExitStack() as stack:
        shared = SharedRuntime(
            StorageResources(
                Mock(), interaction_sessions, redis, interaction_object_store, tmp_path, CapacityLimiter(4)
            ),
            test_lifecycle_writer(),
            SecretProtector(key=b"k" * 32, encryption_key_id="test"),
        )
        runtime, background = await build_worker_runtime(
            settings, shared, resources, EnvironmentProviderCatalog(), stack
        )
        loop = runtime.execution_loop
        assert loop is not None
        assert any(component.run == loop.run for component in background)
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as session:
                        row = await session.get(RunRecord, run.id)
                        assert row is not None
                        if row.status in {"completed", "failed"}:
                            assert row.status == "completed", row.failure_json
                            assert row.output_json == ({"answer": 42} if recover_candidate else "worker completed")
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
        async with short_session(interaction_sessions) as session:
            attempt = await session.scalar(
                select(RunAttemptRecord)
                .where(RunAttemptRecord.run_id == run.id)
                .order_by(RunAttemptRecord.fence.desc())
            )
            assert attempt.status == "succeeded"
            assert (attempt.harness_run_id is None) is recover_candidate
        checkpoint = await states.read(ORGANIZATION_ID, run.id)
        assert checkpoint.envelope.checkpoint_kind == "completed"
        assert checkpoint.envelope.input_disposition == "applied"
        assert checkpoint.writer_fence == (2 if recover_candidate else 1)
        assert len(requests) == (0 if recover_candidate else 1)
        preflight.assert_awaited_once_with(lock)
