"""Production composition tests: only outbound HTTP is replaced, not the executor or Model."""

import json
from contextlib import asynccontextmanager
from time import monotonic

import httpx2
import pytest
from a13n_harness import SafeFailure
from a13n_service.agents.domain import CreateAgentRequest
from a13n_service.app import create_app
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.interactions.command_values import StartRunCommand
from a13n_service.interactions.inbox import RedisThreadControlSignals, ThreadInboxStore
from a13n_service.interactions.input import AcceptedAgentInput
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.models.domain import CreateModelProviderRequest, CreateModelRequest
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.storage import short_session
from anyio import Event, fail_after, sleep, sleep_forever
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from ..interactions.conftest import agent_config
from ..models.conftest import ORG_ID, WORKSPACE_ID, actor, seed_models
from .support import local_settings

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _worker(tmp_path, monkeypatch, handle, **settings):
    async def allow(self, endpoint, *, resolve_dns=True):
        return endpoint

    monkeypatch.setattr(EndpointPolicy, "validate", allow)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as model_http:
        monkeypatch.setattr(
            "a13n_service.process.resources.NativeModelFactory",
            lambda http, registry: NativeModelFactory(model_http, registry),
        )
        app = create_app(
            local_settings(
                tmp_path,
                pricing_auto_update=False,
                observability_tracing=False,
                worker_poll_interval_seconds=0.02,
                **settings,
            )
        )
        async with app.router.lifespan_context(app):
            yield app.state.runtime, app


async def _configure(runtime, config=None, *, base_url=None):
    await seed_models(runtime.shared.storage.sessions)
    control = runtime.control
    provider = await control.model_providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(
            type="openai_compatible" if base_url else "openai",
            name="Worker test",
            credential="test-only",
            configuration={"base_url": base_url} if base_url else {},
        ),
    )
    await control.models.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="primary",
            provider_id=provider.id,
            name="Worker model",
            upstream_model="test-model",
            model_api="openai.chat_completions",
        ),
    )
    created = await control.agents.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="worker-agent",
        request=CreateAgentRequest(name="Worker test", config=config or agent_config()),
    )
    return created.agent.id


async def _start(runtime, agent_id):
    return await runtime.control.gateway.commands.start(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="worker-run",
        request=StartRunCommand(
            agent_id=agent_id, input={"schema_version": "1", "content": [{"type": "text", "text": "hello"}]}
        ),
    )


async def _terminal(runtime, run_id):
    with fail_after(10):
        while True:
            async with short_session(runtime.shared.storage.sessions) as database:
                run = (await database.get(RunRecord, run_id)).to_resource()
            if run.sealed_at is not None:
                return run
            await sleep(0.02)


def _stream_reply():
    chunks = [
        {"role": "assistant", "content": "Worker "},
        {"content": "finished"},
        {},
    ]
    data = "".join(
        "data: "
        + json.dumps(
            {
                "id": "chat-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if index == 2 else None}],
            }
        )
        + "\n\n"
        for index, delta in enumerate(chunks)
    )
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=data + "data: [DONE]\n\n")


async def test_on_demand_process_claims_executes_checkpoints_and_seals(tmp_path, monkeypatch):
    requests = []

    async def allow(self, endpoint, *, resolve_dns=True):
        return endpoint

    async def handle(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-only"
        return _stream_reply()

    monkeypatch.setattr(EndpointPolicy, "validate", allow)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as model_http:
        monkeypatch.setattr(
            "a13n_service.process.resources.NativeModelFactory",
            lambda http, registry: NativeModelFactory(model_http, registry),
        )
        app = create_app(
            local_settings(
                tmp_path, pricing_auto_update=False, observability_tracing=False, worker_poll_interval_seconds=0.02
            )
        )
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            assert runtime.worker.execution_loop.ready
            receipt = await _start(runtime, await _configure(runtime))
            run = await _terminal(runtime, receipt.run_id)
            assert run.status.value == "completed", run.failure
            assert run.output == "Worker finished"
            assert len(requests) == 1
            assert requests[0]["model"] == "test-model"
            assert any(item["role"] == "user" and item["content"] == "hello" for item in requests[0]["messages"])
            assert requests[0]["prompt_cache_key"] == run.thread_id
            state = await RunStateStore(runtime.shared.storage.objects).read(ORG_ID, run.id)
            assert state.digest_sha256 == run.sealed_state.digest_sha256
            assert state.envelope.input_disposition == "applied"
            async with short_session(runtime.shared.storage.sessions) as database:
                attempt = (
                    await database.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id == run.id))
                ).one()
                assert attempt.status == "succeeded"
                assert attempt.harness_run_id is not None
                assert attempt.worker_build_id.startswith("build-")
                assert attempt.finished_at is not None
        assert not runtime.worker.execution_loop.ready
        assert runtime.worker.execution_loop.active_count == 0


async def test_cancel_interrupts_live_native_model_and_releases_capacity(tmp_path, monkeypatch):
    entered, closed = Event(), Event()

    async def handle(request):
        entered.set()
        try:
            await sleep_forever()
        finally:
            closed.set()

    async with _worker(tmp_path, monkeypatch, handle) as (runtime, _):
        accepted = await _start(runtime, await _configure(runtime))
        with fail_after(10):
            await entered.wait()
        sessions = runtime.shared.storage.sessions
        async with short_session(sessions) as database:
            run = (await database.get(RunRecord, accepted.run_id)).to_resource()
            thread_version = (await database.get(ThreadRecord, run.thread_id)).version
        await RunOutcomeService(
            sessions,
            RunPayloadStore(runtime.shared.storage.objects),
            control_signals=RedisThreadControlSignals(runtime.shared.storage.redis),
            lifecycle=test_lifecycle_writer(),
        ).cancel(
            organization_id=ORG_ID,
            run_id=run.id,
            expected_run_version=run.version,
            expected_thread_version=thread_version,
            failure=SafeFailure(code="cancelled", message="Cancelled in the test."),
        )
        with fail_after(10):
            await closed.wait()
            while runtime.worker.execution_loop.active_count:
                await sleep(0.02)
        terminal = await _terminal(runtime, run.id)
        assert terminal.status.value == "cancelled"
        assert runtime.worker.execution_loop.ready


async def test_accepted_output_limit_fails_the_run_without_stopping_worker(tmp_path, monkeypatch):
    async def handle(request):
        return _stream_reply()

    config = agent_config()
    config = config.model_copy(
        update={
            "protocol": config.protocol.model_copy(
                update={
                    "limits": config.protocol.limits.model_copy(update={"max_output_bytes": 5}),
                }
            )
        }
    )
    async with _worker(tmp_path, monkeypatch, handle) as (runtime, _):
        accepted = await _start(runtime, await _configure(runtime, config))
        terminal = await _terminal(runtime, accepted.run_id)
        assert terminal.status.value == "failed"
        assert terminal.failure.code == "output_projection_failed"
        assert runtime.worker.execution_loop.ready


async def test_drain_preserves_renewal_and_allows_an_already_completed_result(tmp_path, monkeypatch):
    entered, release = Event(), Event()

    async def handle(request):
        entered.set()
        await release.wait()
        return _stream_reply()

    async with _worker(tmp_path, monkeypatch, handle, worker_renewal_interval_seconds=0.05) as (runtime, app):
        accepted = await _start(runtime, await _configure(runtime))
        with fail_after(10):
            await entered.wait()
        sessions = runtime.shared.storage.sessions
        async with short_session(sessions) as database:
            before = (
                (await database.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id == accepted.run_id)))
                .one()
                .version
            )
        loop = runtime.worker.execution_loop
        await loop.drain()
        assert not loop.ready
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="http://testserver") as client:
            assert (await client.get("/readyz")).status_code == 503
        with fail_after(10):
            while True:
                async with short_session(sessions) as database:
                    attempt = (
                        (
                            await database.scalars(
                                select(RunAttemptRecord).where(RunAttemptRecord.run_id == accepted.run_id)
                            )
                        )
                        .one()
                        .to_resource()
                    )
                if attempt.version > before:
                    assert attempt.status.value == "running"
                    break
                await sleep(0.02)
        release.set()
        with fail_after(10):
            await loop.wait_stopped()
        async with short_session(sessions) as database:
            run = (await database.get(RunRecord, accepted.run_id)).to_resource()
            attempt = (await database.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id == run.id))).one()
            assert attempt.status == "succeeded"
            assert attempt.yield_reason is None
        assert run.status.value == "completed"
        assert run.output == "Worker finished"
        assert loop.active_count == 0


async def test_shutdown_deadline_cancels_stalled_model_and_joins_attempt(tmp_path, monkeypatch):
    entered, closed = Event(), Event()

    async def handle(request):
        entered.set()
        try:
            await sleep_forever()
        finally:
            closed.set()

    async with _worker(
        tmp_path, monkeypatch, handle, worker_drain_timeout_seconds=0.1, worker_cleanup_timeout_seconds=0.2
    ) as (runtime, _):
        await _start(runtime, await _configure(runtime))
        with fail_after(10):
            await entered.wait()
        start = monotonic()
    assert monotonic() - start < 3
    assert closed.is_set()
    assert runtime.worker.execution_loop.active_count == 0
    assert not runtime.status.startup_complete


async def test_unavailable_steering_asset_fails_only_the_owned_run(tmp_path, monkeypatch):
    entered, closed = Event(), Event()

    async def handle(request):
        entered.set()
        try:
            await sleep_forever()
        finally:
            closed.set()

    async with _worker(tmp_path, monkeypatch, handle) as (runtime, _):
        accepted = await _start(runtime, await _configure(runtime))
        with fail_after(10):
            await entered.wait()
        sessions = runtime.shared.storage.sessions
        # Model an accepted Asset whose access disappeared before live delivery.
        await ThreadInboxStore(sessions, signals=RedisThreadControlSignals(runtime.shared.storage.redis)).append_steer(
            organization_id=ORG_ID,
            run_id=accepted.run_id,
            input=AcceptedAgentInput.model_validate(
                {
                    "schema_version": "2",
                    "content": [
                        {
                            "type": "binary",
                            "source": {"type": "asset", "asset_id": "ast_1234567890abcdef"},
                            "media_type": "application/octet-stream",
                            "delivery": "model_content",
                        }
                    ],
                }
            ),
        )
        terminal = await _terminal(runtime, accepted.run_id)
        assert terminal.status.value == "failed"
        assert terminal.failure.code == "input_materialization_failed"
        assert closed.is_set()
        assert runtime.worker.execution_loop.ready
