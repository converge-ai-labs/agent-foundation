import json
from contextlib import aclosing, asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_environment import (
    FILE_READ_ACTIONS,
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    EnvironmentPermissionSet,
)
from a13n_harness import EnvironmentMount
from a13n_harness.tools.invocation import current_invocation_scope
from a13n_service.agents.domain import PreparedAgentPlugins, SecretRequirement
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.assets.models import AssetRecord
from a13n_service.digests import digest_request
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.attempt_executor import RunAttemptExecutor
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.harness_runtime import SingleHarnessEnvironment
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.protocol_context import ProtocolInputContext
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, WorkerClaim
from a13n_service.interactions.worker_preparation import WorkerAttemptPreparer
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, fail_after, sleep
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.messages import TextContent as NativeTextContent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer
from tests.observability.test_runtime import runtime as observation_runtime

from . import test_attempt_execution as acceptance
from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from .test_agent_secrets import TEST_VALUE, _binding, _secret, _SecretTool
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
    handoff=False,
):
    # Worker recovery must not hide an unexpected executor failure in this success-path test.
    execute = RunAttemptExecutor.run

    async def checked_execute(*args, **kwargs):
        try:
            return await execute(*args, **kwargs)
        except Exception as error:
            pytest.fail(f"Unexpected attempt failure: {error!r}")

    monkeypatch.setattr(RunAttemptExecutor, "run", checked_execute)
    config = acceptance.effective_agent_config()
    config = config.model_copy(
        update={
            "toolsets": {
                **config.toolsets,
                "assets": config.toolsets["assets"].model_copy(update={"enabled": True}),
            },
            "secret_requirements": (SecretRequirement(key="storage"),),
            "protocol": config.protocol.model_copy(
                update={"state_schema": {"type": "object"}, "context_schema": {"type": "array"}}
            ),
        }
    )
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
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
        revision = await session.get(AgentRevisionRecord, run.agent_revision_id)
        toolsets = dict(revision.config["toolsets"])
        toolsets["assets"] = {**toolsets["assets"], "enabled": True}
        revision.config = {**revision.config, "toolsets": toolsets}
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
        initial = await states.claim_writer(
            await states.read(ORGANIZATION_ID, run.id), attempt_number=context.attempt_number
        )
        initial = await states.prepare_plugins(
            initial, PreparedAgentPlugins(plugins=()), attempt_number=context.attempt_number
        )
        await execution.publish_checkpoint(
            context,
            states,
            initial,
            acceptance._completed_state(initial.envelope, claim.attempt.id, claim.attempt.attempt_number),
        )
    if not recover_candidate or late_input:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "result.txt").write_text("worker-produced-asset")
        original_prepare = WorkerAttemptPreparer.open_runtime

        @asynccontextmanager
        async def prepare_with_environment(self, context):
            async with original_prepare(self, context) as invocation:
                environment = DirectLocalEnvironmentProvider().create_environment(
                    configuration=DirectLocalProviderConfiguration(root=DirectLocalRootConfiguration(path=workspace)),
                    environment_id="worker-assets",
                    state=None,
                )
                yield replace(
                    invocation,
                    environment=SingleHarnessEnvironment(
                        EnvironmentMount(
                            environment,
                            permission_ceiling=EnvironmentPermissionSet(operations=FILE_READ_ACTIONS),
                        )
                    ),
                )

        monkeypatch.setattr(WorkerAttemptPreparer, "open_runtime", prepare_with_environment)
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
    handed_off = False
    stream_entry = RunAttemptControl.after_stream_entry

    async def handoff_continuation(control):
        nonlocal handed_off
        if handoff and not handed_off and control.current_state.envelope.outcome_candidate is not None:
            handed_off = True
            await control.request_handoff(RunAttemptYieldReason.service_drain)
        await stream_entry(control)

    monkeypatch.setattr(RunAttemptControl, "after_stream_entry", handoff_continuation)
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
    settings = Settings(
        service={"build_version": "test"},
        worker={"concurrency": 1, "poll_interval_seconds": 0.02, "lease_seconds": 30},
    )
    exporter = InMemorySpanExporter()
    observation = observation_runtime(exporter)
    async with (
        aclosing(observation),
        worker_runtime(
            interaction_sessions,
            interaction_object_store,
            tmp_path,
            monkeypatch,
            settings=settings,
            model_factory=model_factory,
            observability=observation,
        ) as (runtime, _shared),
    ):
        loop = runtime.execution_loop
        assert loop is not None
        # Keep the normal lease and its renewal/reconciliation timeouts under CI load.
        # Same-build handoff deliberately waits one lease before reclaiming.
        # Budget each execution phase separately from that mandatory delay.
        completion_budget = 15 * (2 if handoff else 1) + (settings.worker.lease_seconds if handoff else 0)
        last_progress = None
        try:
            with fail_after(completion_budget):
                async with create_task_group() as tasks:
                    tasks.start_soon(loop.run)
                    while True:
                        async with short_session(interaction_sessions) as session:
                            row = await session.get(RunRecord, run.id)
                            assert row is not None
                            last_progress = (row.status, row.current_run_attempt_id)
                            if row.status in {"completed", "failed"}:
                                assert row.status == "completed", row.failure_json
                                assert row.output_json == (
                                    {"answer": 42} if recover_candidate and not late_input else "worker completed"
                                )
                                break
                        await sleep(0.02)
                    await loop.drain()
                    await loop.wait_stopped()
        except TimeoutError:
            pytest.fail(
                f"Run did not complete within {completion_budget}s: status/attempt={last_progress}, "
                f"handoff_requested={handed_off}, late_input_injected={injected}, model_requests={len(requests)}"
            )
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
        assert checkpoint.writer_fence == (3 if handoff else 2 if recover_candidate or late_input else 1)
        assert len(requests) == (
            0 if recover_candidate and not late_input else 3 + int(late_input and not recover_candidate)
        )
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
                assert attempts[-1].start_reason == (
                    "planned_handoff" if handoff else "lease_expired" if recover_candidate else "pending_input"
                )
                assert len(attempts) == (3 if handoff else 2)
                if handoff:
                    assert handed_off
                    assert attempts[-2].status == "yielded"
                    assert attempts[-2].failure_json is None
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
        assert checkpoint.envelope.protocol_context == protocol_context
        assert consumed == ([] if recover_candidate and not late_input else [TEST_VALUE])
        assert TEST_VALUE not in checkpoint.envelope.model_dump_json()
        if not recover_candidate or late_input:
            async with short_session(interaction_sessions) as session:
                asset = await session.scalar(
                    select(AssetRecord)
                    .join(RunAttemptRecord, AssetRecord.source_run_attempt_id == RunAttemptRecord.id)
                    .where(RunAttemptRecord.run_id == run.id)
                )
                assert asset is not None and asset.filename == "result.txt"
            assert published and published[0]["asset_id"] == asset.id
        assert observation.tracer_provider.force_flush()
        roots = [span for span in exporter.get_finished_spans() if span.name == "a13n.service.run_attempt"]
        assert roots
        for root in roots:
            if root.attributes["a13n.run_attempt.id"] == attempt.id:
                assert root.attributes["output.value"] == (
                    '{"answer":42}' if recover_candidate and not late_input else "worker completed"
                )
                assert root.attributes["a13n.run_attempt.output.capture"] == "captured"
            else:
                # Earlier successful or yielded Attempts did not seal user-visible output.
                assert "output.value" not in root.attributes
                assert root.attributes["a13n.run_attempt.output.capture"] == "not_committed"


@pytest.mark.parametrize("recover_candidate", [False, True])
async def test_postgresql_worker_continues_late_input_in_same_run(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, recover_candidate
):
    await test_worker_claims_and_executes_an_accepted_run_in_process(
        interaction_sessions, interaction_object_store, tmp_path, monkeypatch, recover_candidate, True
    )


@pytest.mark.parametrize("recover_candidate", [False, True])
async def test_worker_handoff_waits_for_late_input_incorporation(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, recover_candidate
):
    sessions = interaction_sessions
    await test_worker_claims_and_executes_an_accepted_run_in_process(
        sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        recover_candidate=recover_candidate,
        late_input=True,
        handoff=True,
    )
