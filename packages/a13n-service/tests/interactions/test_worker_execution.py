import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_environment import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness import EnvironmentAccess, EnvironmentMount
from a13n_harness.tools.invocation import current_invocation_scope
from a13n_service.agents.domain import AssetPublicationConfig, SecretRequirement, canonical_digest
from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.assets.models import AssetRecord
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.harness_runtime import SingleHarnessEnvironment
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.protocol_context import ProtocolInputContext
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, WorkerClaim
from a13n_service.interactions.worker_preparation import WorkerAttemptPreparer
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.plugins.models import PluginRuntimeLockRecord
from a13n_service.plugins.runtime import PluginRuntimeLock, default_runtime_target, installed_harness_version
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, fail_after, sleep
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID, effective_agent_config
from .test_agent_secrets import TEST_VALUE, _binding, _secret, _SecretTool
from .worker_helpers import worker_runtime

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
            "asset_publication": AssetPublicationConfig(),
            "secret_requirements": (SecretRequirement(key="storage"),),
            "protocol": config.protocol.model_copy(
                update={"state_schema": {"type": "object"}, "context_schema": {"type": "array"}}
            ),
        }
    )
    config = config.model_copy(
        update={
            "content_digest": canonical_digest(
                config.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
            )
        }
    )
    monkeypatch.setattr(acceptance, "effective_agent_config", lambda: config)
    protocol_context = ProtocolInputContext.model_validate(
        {"state": {"locale": "zh-CN"}, "context": [{"description": "Customer tier", "value": "enterprise"}]}
    )
    initialize = acceptance.initialize_start_state

    def initialize_with_context(seed, *, thread_id):
        return initialize(
            seed.model_copy(update={"protocol_context": protocol_context, "secret_bindings": (_binding(),)}),
            thread_id=thread_id,
        )

    monkeypatch.setattr(acceptance, "initialize_start_state", initialize_with_context)
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
    await _secret(interaction_sessions)
    consumed = []

    def consume():
        consumed.append(current_invocation_scope().credentials["storage"])
        return "authenticated"

    provided = AgentReconstructor._provided_capabilities
    monkeypatch.setattr(
        AgentReconstructor,
        "_provided_capabilities",
        lambda self, node: (*provided(self, node), _SecretTool(consume)),
    )
    if recover_candidate:
        monkeypatch.setattr(
            "a13n_service.interactions.worker_preparation.WorkerAttemptPreparer.prepare",
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
    else:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "result.txt").write_text("worker-produced-asset")
        environment = DirectLocalEnvironmentProvider().create_environment(
            configuration=DirectLocalProviderConfiguration(root=DirectLocalRootConfiguration(path=workspace)),
            environment_id="worker-assets",
            state=None,
        )
        original_prepare = WorkerAttemptPreparer.prepare

        @asynccontextmanager
        async def prepare_with_environment(self, context):
            async with original_prepare(self, context) as invocation:
                yield replace(
                    invocation,
                    environment=SingleHarnessEnvironment(
                        EnvironmentMount(environment, access=EnvironmentAccess.READ_ONLY)
                    ),
                )

        monkeypatch.setattr(WorkerAttemptPreparer, "prepare", prepare_with_environment)
    requests = []
    published = []

    async def model(messages, info):
        requests.append(messages)
        assert "zh-CN" in repr(messages) and "enterprise" in repr(messages)
        assert "publish_asset" in {tool.name for tool in info.function_tools}
        if len(requests) == 1:
            yield {
                0: DeltaToolCall(
                    name="publish_asset",
                    json_args=json.dumps({"path": "/workspace/result.txt"}),
                    tool_call_id="publish-file",
                )
            }
        elif len(requests) == 2:
            yield {0: DeltaToolCall(name="use_credential", json_args="{}", tool_call_id="use-secret")}
        else:
            published.extend(
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "publish_asset"
            )
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
        assert checkpoint.envelope.protocol_context == protocol_context
        assert len(requests) == (0 if recover_candidate else 3)
        assert consumed == ([] if recover_candidate else [TEST_VALUE])
        assert TEST_VALUE not in checkpoint.envelope.model_dump_json()
        if not recover_candidate:
            async with short_session(interaction_sessions) as session:
                asset = await session.scalar(select(AssetRecord).where(AssetRecord.source_run_attempt_id == attempt.id))
                assert asset is not None and asset.filename == "result.txt"
            assert published and published[0].asset_id == asset.id
        preflight.assert_awaited_once_with(lock)
