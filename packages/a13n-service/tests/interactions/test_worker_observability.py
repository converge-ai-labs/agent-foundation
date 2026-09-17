"""Trace assertions through the real Worker, persistence, and Environment lifecycle."""

import json
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.state import RunPayloadEnvelope
from a13n_service.interactions.worker_preparation import WorkerAttemptPreparer
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.observability import TraceContent
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import ObjectStoreUnavailable
from anyio import create_task_group, fail_after, sleep
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from tests.hooks.support import seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer
from tests.observability.test_runtime import runtime as observation_runtime

from .conftest import NOW
from .test_attempt_execution import _accept_root, _worker
from .test_environment_runtime import template_config
from .worker_helpers import prepare_permissions, worker_runtime

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "on_run",
        "on_use_unused",
        "reconstruct_failure",
        "persist_failure",
        "retry",
        "takeover",
        "external_input",
        "external_output",
        "content_none",
        "content_full",
    ],
)
async def test_worker_trace_matches_real_phase_boundaries_and_durable_outcomes(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, scenario
):
    environment_catalog = None
    if scenario in {"on_run", "on_use_unused"}:
        await template_config(interaction_sessions, tmp_path, "on_run" if scenario == "on_run" else "on_use")
        environment_catalog = build_environment_provider_catalog(builtin_keys=("direct-local",))
    else:
        await seed_hook_actor_access(interaction_sessions)
    _, run, _ = await _accept_root(
        interaction_sessions, interaction_object_store, max_attempts=2 if scenario in {"retry", "takeover"} else 1
    )
    if scenario == "external_input":
        reference = await RunPayloadStore(interaction_object_store).create(
            run.organization_id,
            RunPayloadEnvelope(run_id=run.id, payload_kind="input", payload_schema_version="1", payload=run.input),
        )
        async with transaction(interaction_sessions) as session:
            row = await session.get(RunRecord, run.id)
            row.input_json = None
            row.input_object_key = reference.object_key
            row.input_object_digest_sha256 = reference.digest_sha256
            row.input_object_size_bytes = reference.size_bytes
            row.input_object_content_type = reference.content_type
            row.input_object_schema_version = reference.schema_version
    if scenario == "takeover":
        claim = await AttemptScheduler(
            interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
        ).claim(run.id, _worker())
        assert isinstance(claim, ClaimedAttempt)

    if scenario == "persist_failure":
        monkeypatch.setattr(
            RunOutcomeService,
            "commit_verified_state_outcome",
            AsyncMock(side_effect=ObjectStoreUnavailable("private persistence details")),
        )
    elif scenario in {"reconstruct_failure", "retry"}:
        validate = WorkerAttemptPreparer.validate_dependencies
        failed_once = False

        async def validate_once(self, context):
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise ObjectStoreUnavailable("private dependency details")
            await validate(self, context)

        monkeypatch.setattr(WorkerAttemptPreparer, "validate_dependencies", validate_once)

    requests = []
    output = "large output " * 6000 if scenario == "external_output" else "observed worker output"

    async def respond(messages, _info):
        requests.append(messages)
        yield output

    model_factory = Mock(spec=NativeModelFactory)
    model_factory.build.return_value = FunctionModel(stream_function=respond)
    settings = Settings(service={"build_version": "test"}, worker={"concurrency": 1, "poll_interval_seconds": 0.01})
    exporter = InMemorySpanExporter()
    content = {"content_none": TraceContent.none, "content_full": TraceContent.full}.get(
        scenario, TraceContent.standard
    )
    observation = observation_runtime(exporter, content=content)
    try:
        async with worker_runtime(
            interaction_sessions,
            interaction_object_store,
            tmp_path,
            monkeypatch,
            settings=settings,
            model_factory=model_factory,
            observability=observation,
            environment_catalog=environment_catalog,
        ) as (worker, _shared):
            loop = worker.execution_loop
            with fail_after(15):
                async with create_task_group() as tasks:
                    tasks.start_soon(loop.run)
                    while True:
                        async with short_session(interaction_sessions) as session:
                            current = await session.get(RunRecord, run.id)
                            if current.status in {"completed", "failed"}:
                                assert current.status == (
                                    "failed" if scenario in {"reconstruct_failure", "persist_failure"} else "completed"
                                ), current.failure_json
                                break
                        await sleep(0.01)
                    await loop.drain()
                    await loop.wait_stopped()

        async with short_session(interaction_sessions) as session:
            attempts = [
                row.to_resource()
                for row in await session.scalars(
                    select(RunAttemptRecord)
                    .where(RunAttemptRecord.run_id == run.id)
                    .order_by(RunAttemptRecord.attempt_number)
                )
            ]
        assert observation.tracer_provider.force_flush()
        spans = exporter.get_finished_spans()
        roots = [span for span in spans if span.name == "a13n.service.run_attempt"]
        observed_attempts = attempts[1:] if scenario == "takeover" else attempts
        assert len(roots) == len(observed_attempts)
        assert len({span.context.trace_id for span in roots}) == len(roots)
        for attempt in observed_attempts:
            root = next(span for span in roots if span.attributes["a13n.run_attempt.id"] == attempt.id)
            assert root.parent is None
            assert root.attributes["a13n.run_attempt.outcome"] == attempt.status.value
            assert root.attributes["a13n.service.run.id"] == run.id
            assert root.attributes["a13n.thread.id"] == run.thread_id
            if content is TraceContent.none:
                assert "input.value" not in root.attributes
                assert "output.value" not in root.attributes
                assert root.attributes["a13n.run_attempt.output.capture"] == "content_disabled"
            else:
                assert json.loads(root.attributes["input.value"]) == run.input
                assert root.attributes["a13n.run_attempt.input.capture"] == "captured"
                if attempt.status.value == "succeeded":
                    assert root.attributes["output.value"] == output
                    assert root.attributes["output.mime_type"] == "text/plain"
                    assert root.attributes["a13n.run_attempt.output.capture"] == "captured"
                else:
                    assert "output.value" not in root.attributes
                    assert root.attributes["a13n.run_attempt.output.capture"] == "not_committed"
            if attempt.failure is not None:
                assert root.attributes["a13n.run_attempt.failure.code"] == attempt.failure.code
            else:
                assert "a13n.run_attempt.failure.code" not in root.attributes
            if attempt.attempt_number == 1:
                assert "a13n.run_attempt.recovery.reason" not in root.attributes
            else:
                assert root.attributes["a13n.run_attempt.recovery.reason"] == (
                    "lease_expired" if scenario == "takeover" else "retry_after_failure"
                )
                assert root.attributes["a13n.run_attempt.replaces.id"] == attempts[0].id

            subtree = [span for span in spans if span.context.trace_id == root.context.trace_id]
            phases = [
                span
                for span in subtree
                if span.name in {"a13n.service.reconstruct", "a13n.service.environment.prepare", "a13n.service.persist"}
            ]
            assert any(span.name == "a13n.service.reconstruct" for span in phases)
            assert any(span.name == "a13n.service.persist" for span in phases)
            for phase in phases:
                assert phase.parent.span_id == root.context.span_id
                assert root.start_time <= phase.start_time <= phase.end_time <= root.end_time
                assert "private" not in str(phase.attributes)
                assert not phase.events
                assert (
                    phase.attributes["langfuse.observation.metadata.service_phase_outcome"]
                    == phase.attributes["a13n.service.phase.outcome"]
                )
                if phase.name == "a13n.service.persist":
                    assert phase.attributes["a13n.service.phase.operation"] in {"finalize", "failure_decision"}
                if phase.attributes["a13n.service.phase.outcome"] == "succeeded":
                    if content is TraceContent.none:
                        assert "output.value" not in phase.attributes
                    else:
                        summary = json.loads(phase.attributes["output.value"])
                        assert summary
                        if phase.name == "a13n.service.reconstruct":
                            assert summary["disposition"] == "ready_for_harness"
            harness = next((span for span in subtree if span.name == "harness.run"), None)
            if attempt.harness_run_id is not None:
                assert harness is not None
                assert harness.parent.span_id == root.context.span_id
                reconstruct = next(span for span in phases if span.name == "a13n.service.reconstruct")
                assert reconstruct.end_time <= harness.start_time
                assert all(
                    span.start_time >= harness.end_time for span in phases if span.name == "a13n.service.persist"
                )
            else:
                assert harness is None
            environment_spans = [span for span in phases if span.name == "a13n.service.environment.prepare"]
            assert len(environment_spans) == (1 if scenario == "on_run" else 0)
            if environment_spans:
                assert environment_spans[0].attributes["a13n.environment.generation"] == 1
                assert environment_spans[0].attributes["a13n.environment.generation_before"] == 0
                assert environment_spans[0].end_time <= harness.start_time
            if scenario in {"reconstruct_failure", "persist_failure", "retry"} and attempt.attempt_number == 1:
                failed_name = "a13n.service.persist" if scenario == "persist_failure" else "a13n.service.reconstruct"
                failed_phase = next(span for span in phases if span.name == failed_name)
                assert failed_phase.status.status_code is StatusCode.ERROR
                assert failed_phase.attributes["a13n.service.phase.outcome"] == "failed"
                assert failed_phase.attributes["error.type"] == "ObjectStoreUnavailable"
            else:
                assert all(span.attributes["a13n.service.phase.outcome"] == "succeeded" for span in phases)
        assert len(requests) == (0 if scenario == "reconstruct_failure" else 1)
    finally:
        await observation.aclose()


async def test_lazy_environment_span_times_actual_first_use(interaction_sessions, interaction_object_store, tmp_path):
    from a13n_service.environments.runtime import prepare_run_environment

    from tests.observability.test_runtime import correlation

    from .test_attempt_execution import _authority

    _, _, lifecycle = await template_config(interaction_sessions, tmp_path, "on_use")
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    exporter = InMemorySpanExporter()
    observation = observation_runtime(exporter)
    provider = observation.tracer_provider
    try:
        with observation.run_attempt(correlation()):
            environment = await prepare_run_environment(
                lifecycle, await prepare_permissions(interaction_sessions, run, _authority(claim))
            )
            assert environment is not None
            try:
                await environment.enter(
                    thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent-1", mount_id="workspace"
                )
                assert provider.force_flush()
                assert not exporter.get_finished_spans()
                # A first-use prepare is a root child even when initiated from a tool.
                with provider.get_tracer("pydantic-ai").start_as_current_span("tool"):
                    await environment.ensure_ready(frozenset({"files"}))
                    await environment.operations.files.write_text("/first-use.txt", "ready", mode="create")
                    await environment.ensure_ready(frozenset({"files"}))
            finally:
                await environment.close()
        assert provider.force_flush()
        spans = exporter.get_finished_spans()
        root = next(span for span in spans if span.name == "a13n.service.run_attempt")
        tool = next(span for span in spans if span.name == "tool")
        phases = [span for span in spans if span.name == "a13n.service.environment.prepare"]
        assert len(phases) == 1
        phase = phases[0]
        assert phase.parent.span_id == root.context.span_id
        assert tool.start_time <= phase.start_time <= phase.end_time <= tool.end_time
        assert phase.attributes["a13n.environment.id"] == environment.environment_id
        assert phase.attributes["a13n.environment.generation"] == 1
        assert phase.attributes["a13n.service.phase.outcome"] == "succeeded"
        assert (tmp_path / "environments" / environment.environment_id / "first-use.txt").read_text() == "ready"
    finally:
        await observation.aclose()


@pytest.mark.parametrize("waiting", [False, True])
async def test_recovered_candidate_captures_only_confirmed_output_without_extra_payload_reads(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, waiting
):
    from contextlib import aclosing

    from a13n_service.agents.domain import PreparedAgentPlugins
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.state import CompletedOutcomeCandidate

    from .test_attempt_execution import _authority, _completed_state, _waiting_state

    await seed_hook_actor_access(interaction_sessions)
    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    authority = _authority(claim)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    prepared = await execution.commit_preparation_success(authority)
    await execution.enter_harness(authority, preparation=prepared, harness_run_id="prior-harness-run")
    initial = await states.claim_writer(await states.read(run.organization_id, run.id), attempt_number=1)
    initial = await states.prepare_plugins(initial, PreparedAgentPlugins(plugins=()), attempt_number=1)
    if waiting:
        candidate = _waiting_state(initial.envelope, claim.attempt.id, 1)
    else:
        reference = await RunPayloadStore(interaction_object_store).create(
            run.organization_id,
            RunPayloadEnvelope(
                run_id=run.id, payload_kind="output", payload_schema_version="1", payload={"answer": 42}
            ),
        )
        candidate = _completed_state(
            initial.envelope, claim.attempt.id, 1, outcome=CompletedOutcomeCandidate(output_object=reference)
        )
    await execution.publish_checkpoint(authority, states, initial, candidate)
    monkeypatch.setattr(
        WorkerAttemptPreparer, "open_runtime", AsyncMock(side_effect=AssertionError("Recovery must not enter Harness"))
    )
    payload_reads = []
    read = RunPayloadStore.read

    async def record_read(self, organization_id, reference):
        payload_reads.append(reference.digest_sha256)
        return await read(self, organization_id, reference)

    monkeypatch.setattr(RunPayloadStore, "read", record_read)
    exporter = InMemorySpanExporter()
    observation = observation_runtime(exporter)
    settings = Settings(service={"build_version": "test"}, worker={"concurrency": 1, "poll_interval_seconds": 0.01})
    async with (
        aclosing(observation),
        worker_runtime(
            interaction_sessions,
            interaction_object_store,
            tmp_path,
            monkeypatch,
            settings=settings,
            observability=observation,
            model_factory=Mock(spec=NativeModelFactory),
        ) as (worker, _shared),
    ):
        loop = worker.execution_loop
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(interaction_sessions) as session:
                        current = await session.get(RunRecord, run.id)
                        if current.status in {"waiting", "completed", "failed"}:
                            assert current.status == ("waiting" if waiting else "completed"), current.failure_json
                            break
                    await sleep(0.01)
                await loop.drain()
                await loop.wait_stopped()
        assert observation.tracer_provider.force_flush()
        spans = exporter.get_finished_spans()
        roots = [span for span in spans if span.name == "a13n.service.run_attempt"]
        assert len(roots) == 1
        root = roots[0]
        assert root.attributes["a13n.run_attempt.outcome"] == "succeeded"
        assert not any(span.name == "harness.run" for span in spans)
        expected = "waiting" if waiting else "completed"
        assert root.attributes["a13n.run_attempt.disposition"] == expected
        reconstruct = next(span for span in spans if span.name == "a13n.service.reconstruct")
        persist = next(span for span in spans if span.name == "a13n.service.persist")
        assert json.loads(reconstruct.attributes["output.value"])["disposition"] == "saved_outcome_available"
        assert persist.attributes["a13n.service.phase.operation"] == "recover_outcome"
        assert json.loads(persist.attributes["output.value"])["disposition"] == expected
        if waiting:
            assert persist.attributes["a13n.service.phase.pending_call_count"] == 1
            assert "output.value" not in root.attributes
            assert root.attributes["a13n.run_attempt.output.capture"] == "not_committed"
            assert payload_reads == []
        else:
            assert root.attributes["output.value"] == '{"answer":42}'
            assert root.attributes["output.mime_type"] == "application/json"
            assert root.attributes["a13n.run_attempt.output.capture"] == "captured"
            # The existing integrity verification reads once; tracing performs no second read.
            assert payload_reads == [reference.digest_sha256]
