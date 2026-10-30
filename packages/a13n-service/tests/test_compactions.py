"""Real storage and model dispatch for detached compaction lifecycle boundaries."""

import asyncio
import json

import pytest
from a13n_harness.compaction import CompactionInput, CompactionResult
from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.outbox import OutboxRow
from a13n_service.runs import compactions
from a13n_service.runs.attempts import AttemptControl, LeaseLost
from a13n_service.runs.claim import claim
from a13n_service.runs.compaction_model import CompactionRun, Selection
from a13n_service.runs.execute import _Attempt, _plan
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.seal import seal_attempt
from a13n_service.runs.tables import CompactionRow, RunRow, ThreadRow, UsageRecordRow
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio


def frozen(lease, *, identity="compaction-test", model_id="model"):
    return CompactionInput.create(
        operation_id=identity,
        thread_id=lease.thread_id,
        run_id="harness-run",
        model_id=model_id,
        model_name="scripted",
        provider_name="openai",
        messages=[
            ModelRequest(parts=[UserPromptPart("Earlier question")]),
            ModelResponse(parts=[TextPart("Earlier answer")]),
        ],
        parameters=ModelRequestParameters(),
    )


async def setup(service, scripted_model, runs_kit, *, background=True):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, background_compaction={"enabled": background})
    await runs_kit.start_thread(service, agent, "hi")
    [lease] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    return lease


async def test_one_unsettled_operation_and_independent_owner_expiry(service, scripted_model, runs_kit):
    lease = await setup(service, scripted_model, runs_kit)
    input = frozen(lease)
    owners = await asyncio.gather(*(compactions.reserve(service.runtime, lease, input) for _ in range(2)))
    [owner] = [owner for owner in owners if owner is not None]
    await compactions.publish(service.runtime, owner, input)
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(CompactionRow)
            .where(CompactionRow.id == owner.operation_id)
            .values(lease_expires_at=CompactionRow.created_at)
        )
    observed = await compactions.observe(service.runtime, lease.thread_id)
    assert observed is not None
    operation = await compactions.load(service.runtime, observed)
    assert (operation.status, operation.error_code) == ("failed", "compaction_interrupted")
    assert await compactions.reserve(service.runtime, lease, frozen(lease, identity="other")) is None
    with pytest.raises(LeaseLost):
        await compactions.publish(service.runtime, owner, CompactionResult.from_summary(input.basis, "late"))
    with pytest.raises(LeaseLost):
        await compactions.renew(service.runtime, owner)


async def test_settlement_and_cleanup_rollback_together(service, scripted_model, runs_kit):
    lease = await setup(service, scripted_model, runs_kit)
    input = frozen(lease)
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    result = CompactionResult.from_summary(input.basis, "summary")
    await compactions.publish(service.runtime, owner, result)
    await compactions.publish(service.runtime, owner, result)
    observed = await compactions.observe(service.runtime, lease.thread_id)
    assert observed is not None
    ack = compactions.Acknowledgement(observed.id, observed.input_digest, "applied")

    async def commit(*, rollback):
        async with transaction(service.runtime.storage) as session:
            await lock(session, ThreadRow, lease.thread_id)
            await compactions.acknowledge(session, service.runtime, lease.thread_id, ack, at=await now(session))
            if rollback:
                raise ValueError("checkpoint CAS failed")

    with pytest.raises(ValueError, match="CAS"):
        await commit(rollback=True)
    assert await compactions.observe(service.runtime, lease.thread_id) is not None
    async with transaction(service.runtime.storage) as session:
        assert not list(await session.scalars(select(OutboxRow).where(OutboxRow.kind == "checkpoint_cleanup")))
    await commit(rollback=False)
    assert await compactions.observe(service.runtime, lease.thread_id) is None
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(CompactionRow, owner.operation_id)
        assert (row.status, row.disposition) == ("ready", "applied")
        [cleanup] = list(await session.scalars(select(OutboxRow).where(OutboxRow.kind == "checkpoint_cleanup")))
        assert cleanup.payload["keys"] == [
            compactions.key(lease.workspace_id, owner.operation_id, kind) for kind in ("input", "result")
        ]


async def test_archive_fences_late_publication(service, scripted_model, runs_kit):
    lease = await setup(service, scripted_model, runs_kit)
    input = frozen(lease)
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    async with transaction(service.runtime.storage) as session:
        thread = await lock(session, ThreadRow, lease.thread_id)
        assert thread is not None
        thread.archived_at = await now(session)
        await compactions.archive(session, service.runtime, thread.id, at=thread.archived_at)
    with pytest.raises(LeaseLost):
        await compactions.publish(service.runtime, owner, CompactionResult.from_summary(input.basis, "late"))
    assert await compactions.observe(service.runtime, lease.thread_id) is None


async def test_generation_outlives_seal_and_cancelled_waiter_with_late_usage(service, scripted_model, runs_kit, caplog):
    from pydantic_ai.messages import InstructionDeltaPart, ModelMessagesTypeAdapter, ModelSettingsDeltaPart

    lease = await setup(service, scripted_model, runs_kit)
    model_url = f"{service.api}/models/scripted"
    current = await service.client.get(model_url)
    updated = await service.client.patch(
        model_url,
        json={"config": {"model_name": "gpt-5", "model_api": "openai.chat_completions"}},
        headers={"if-match": current.headers["etag"]},
    )
    assert updated.status_code == 200, updated.text
    plan = await _plan(service.runtime, lease)
    attempt = _Attempt(service.runtime, lease, AttemptControl(), plan)
    input = frozen(lease, model_id=plan.agent.model.key)
    messages = input.request_messages
    messages.insert(
        -1,
        ModelRequest(
            parts=[
                InstructionDeltaPart(id="capability:a13n.codeact:catalog", content="def lookup(): ..."),
                ModelSettingsDeltaPart(settings={"thinking": "low"}),
            ]
        ),
    )
    input = input.model_copy(
        update={
            "request_json": ModelMessagesTypeAdapter.dump_json(messages).decode(),
            "settings_json": json.dumps({"thinking": "high"}),
        }
    )
    operator = CompactionRun(
        service.runtime.compactions,
        lease,
        Selection(
            plan.agent.model,
            attempt._call_context(),
            plan.principal.id,
            plan.request_context,
            plan.configuration,
            2,
        ),
    )
    gate = asyncio.Event()
    scripted_model.say("Detached summary", gate=gate)
    operation = await operator.submit(input)
    assert operation.status == "running"
    request = await scripted_model.request()
    waiter = asyncio.create_task(operator.wait(lease.thread_id, input.basis.operation_id))
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await seal_attempt(service.runtime, lease, Outcome.cancelled())
    gate.set()
    async with asyncio.timeout(10):
        operation = await operator.wait(lease.thread_id, input.basis.operation_id)
    assert operation.status == "ready", [record.__dict__.get("exception_details") for record in caplog.records]
    assert operation.result.summary == "Detached summary"
    assert request["reasoning_effort"] == "high"
    assert "def lookup(): ..." in json.dumps(request["messages"])
    assert input.request_messages[-2].parts[-1] == ModelSettingsDeltaPart(settings={"thinking": "low"})
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, lease.run_id)
        records = list(
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == run.id,
                    UsageRecordRow.record["kind"].astext == "model",
                )
            )
        )
        assert len(records) == 1
        assert records[0].record["source"] == "compaction"
        assert records[0].model_id == plan.agent.model.id
        assert run.status == "cancelled"
        assert run.usage_at_seal["requests"] == 0


@pytest.mark.parametrize("next_run", [False, True], ids=["same-run", "next-run"])
async def test_real_runs_overlap_then_commit_compaction(
    skill_executing, scripted_model, runs_kit, monkeypatch, next_run
):
    service = skill_executing

    async def count_tokens(self, messages, model_settings, model_request_parameters):
        # The local Chat Completions fixture has no counting endpoint. Only counting
        # is scripted: execution, HTTP requests, checkpoints and settlement are real.
        return RequestUsage(input_tokens=100)

    monkeypatch.setattr(OpenAIChatModel, "count_tokens", count_tokens)
    agent = await runs_kit.create_agent(
        service,
        scripted_model,
        background_compaction={"enabled": True},
        model_settings={"max_tokens": 100},
        model_characteristics={"context_window_tokens": 10000, "compact_threshold": 0.001},
        **runs_kit.LOOKUP,
    )
    scripted_model.say("Earlier answer")
    first = await runs_kit.start_thread(service, agent, "Earlier question")
    assert (await runs_kit.sealed(service, first["run"]["id"]))["status"] == "completed"
    await scripted_model.request()
    thread_id = first["thread"]["id"]
    summary_gate, foreground_gate = asyncio.Event(), asyncio.Event()
    scripted_model.say("Detached summary", to="compact continuation summary", gate=summary_gate)
    if next_run:
        scripted_model.say("Foreground finished", to="Continue now", gate=foreground_gate)
    else:
        runs_kit.lookup(scripted_model, call_id="call_more", to="Continue now", gate=foreground_gate)
    response = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "Continue now"))
    assert response.status_code == 201, response.text
    second = response.json()["run"]
    requests = [await scripted_model.request(), await scripted_model.request()]
    assert sorted(request.get("stream", False) for request in requests) == [False, True]
    operation = await service.runtime.compactions.inspect(thread_id)
    assert operation is not None and operation.status == "running"
    operation_id = operation.input.basis.operation_id
    # Both HTTP requests reached the provider while the summary remains blocked.
    if next_run:
        foreground_gate.set()
        assert (await runs_kit.sealed(service, second["id"]))["status"] == "completed"
    summary_gate.set()
    async with asyncio.timeout(10):
        ready = await service.runtime.compactions.wait(thread_id, operation_id)
    assert ready.status == "ready"
    scripted_model.say("Final answer")
    if next_run:
        response = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "Use the summary"))
        assert response.status_code == 201, response.text
        final_id = response.json()["run"]["id"]
    else:
        foreground_gate.set()
        final_id = second["id"]
    final_request = await scripted_model.request()
    assert "Detached summary" in json.dumps(final_request["messages"])
    assert (await runs_kit.sealed(service, final_id))["output"] == "Final answer"
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(CompactionRow, operation_id)
        assert (row.status, row.disposition) == ("ready", "applied")
        assert row.run_id == second["id"]
        final = await session.get_one(RunRow, final_id)
        assert final.checkpoint is not None
        records = list(
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == second["id"], UsageRecordRow.record["source"].astext == "compaction"
                )
            )
        )
        assert len(records) == 1


@pytest.mark.parametrize("disposition", ["applied", "skipped"])
@pytest.mark.parametrize("background", [True, False])
async def test_exact_checkpoint_acknowledgement_survives_cas_failure(
    service, scripted_model, runs_kit, monkeypatch, disposition, background
):
    from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
    from a13n_harness.compaction import compaction_disposition
    from a13n_service.runs import checkpoints
    from pydantic_ai.models.function import FunctionModel

    lease = await setup(service, scripted_model, runs_kit, background=background)
    input = frozen(lease)
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    result = CompactionResult.from_summary(input.basis, "summary")
    await compactions.publish(service.runtime, owner, result)
    previous = HarnessState.new(thread_id=lease.thread_id, message_history=input.request_messages[:-1])
    if disposition == "skipped":
        previous = HarnessState.new(
            thread_id=lease.thread_id, message_history=[ModelRequest(parts=[UserPromptPart("replaced")])]
        )

    async def answer(messages, info):
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=answer), output_type=str)
    completed = await executable.run("continue", previous_state=previous, compaction_result=result)
    state = completed.state
    assert state is not None
    assert await compaction_disposition(state, basis=input.basis) == disposition
    plan = await _plan(service.runtime, lease)
    assert plan.background_compaction is background
    assert plan.compaction_result == result
    attempt = _Attempt(service.runtime, lease, AttemptControl(), plan)
    commit = checkpoints.commit

    async def reject(*args, **kwargs):
        await commit(*args, **kwargs)
        raise ValueError("checkpoint CAS failed")

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "commit", reject)
        with pytest.raises(ValueError, match="CAS"):
            await attempt._commit(state, cursors={}, snapshot=attempt.fold.snapshot())
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(CompactionRow, owner.operation_id)
        run = await session.get_one(RunRow, lease.run_id)
        assert row.settled_at is None and run.checkpoint is None
    # Committing an older checkpoint cannot acknowledge a newer in-memory decision.
    await attempt._commit(previous, cursors={}, snapshot=attempt.fold.snapshot())
    assert await compactions.observe(service.runtime, lease.thread_id) is not None
    await attempt._commit(state, cursors={}, snapshot=attempt.fold.snapshot())
    assert await compactions.observe(service.runtime, lease.thread_id) is None
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(CompactionRow, owner.operation_id)
        assert row.disposition == disposition
        run = await session.get_one(RunRow, lease.run_id)
        pointer = checkpoints.StatePointer.model_validate(run.checkpoint)
    saved = checkpoints.RunState.model_validate_json(await checkpoints.load(service.runtime.objects, pointer))
    assert await compaction_disposition(saved.harness, basis=input.basis) == disposition
    assert (await _plan(service.runtime, lease)).compaction_result is None


async def test_retention_waits_for_idle_thread(service, scripted_model, runs_kit):
    from datetime import timedelta

    lease = await setup(service, scripted_model, runs_kit)
    input = frozen(lease)
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    async with transaction(service.runtime.storage) as session:
        row, current = await compactions.lock_owner(session, owner)
        row.status = "failed"
        row.error_code = "compaction_failed"
        row.completed_at = current - timedelta(days=8)
    await compactions.sweep(service.runtime)
    assert await compactions.observe(service.runtime, lease.thread_id) is not None
    await seal_attempt(service.runtime, lease, Outcome.cancelled())
    await compactions.sweep(service.runtime)
    await compactions.sweep(service.runtime)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(CompactionRow, owner.operation_id)
        assert (row.status, row.disposition) == ("failed", "expired")
        cleanups = list(await session.scalars(select(OutboxRow).where(OutboxRow.kind == "checkpoint_cleanup")))
        assert len([delivery for delivery in cleanups if "keys" in delivery.payload]) == 1


@pytest.mark.parametrize("expired_owner", [False, True], ids=["idle-retention", "expired-owner"])
async def test_retention_ineligible_thread_does_not_starve_sweep(service, scripted_model, runs_kit, expired_owner):
    from datetime import timedelta

    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(service, agent, "active")
    [active] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    await runs_kit.start_thread(service, agent, "later")
    [later] = await claim(service.runtime, worker_id="test", worker_build="test", limit=1)
    owners = []
    for lease, identity in ((active, "compaction-active"), (later, "compaction-later")):
        input = frozen(lease, identity=identity)
        owner = await compactions.reserve(service.runtime, lease, input)
        assert owner is not None
        await compactions.publish(service.runtime, owner, input)
        owners.append(owner)
    if not expired_owner:
        await seal_attempt(service.runtime, later, Outcome.cancelled())
    async with transaction(service.runtime.storage) as session:
        current = await now(session)
        for index, owner in enumerate(owners):
            row = await session.get_one(CompactionRow, owner.operation_id)
            row.created_at = current - timedelta(days=10 - index)
            if index == 1 and expired_owner:
                row.lease_expires_at = current - timedelta(seconds=1)
            else:
                row.status = "failed"
                row.error_code = "compaction_failed"
                row.completed_at = current - timedelta(days=8)
    await compactions.sweep(service.runtime, limit=1)
    async with transaction(service.runtime.storage) as session:
        first = await session.get_one(CompactionRow, owners[0].operation_id)
        second = await session.get_one(CompactionRow, owners[1].operation_id)
        assert first.settled_at is None
        if expired_owner:
            assert (second.status, second.error_code) == ("failed", "compaction_interrupted")
        else:
            assert second.disposition == "expired"


async def test_archive_during_upload_keeps_retryable_cleanup(service, scripted_model, runs_kit, monkeypatch):
    lease = await setup(service, scripted_model, runs_kit)
    input = frozen(lease)
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    entered, release = asyncio.Event(), asyncio.Event()
    put = service.runtime.objects.put

    async def delayed(key, data, *, content_type):
        entered.set()
        await release.wait()
        return await put(key, data, content_type=content_type)

    monkeypatch.setattr(service.runtime.objects, "put", delayed)
    task = asyncio.create_task(
        compactions.publish(service.runtime, owner, CompactionResult.from_summary(input.basis, "late"))
    )
    await asyncio.wait_for(entered.wait(), 5)
    async with transaction(service.runtime.storage) as session:
        await lock(session, ThreadRow, lease.thread_id)
        await compactions.archive(session, service.runtime, lease.thread_id, at=await now(session))
    release.set()
    with pytest.raises(LeaseLost):
        await task
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(CompactionRow, owner.operation_id)
        deliveries = list(await session.scalars(select(OutboxRow).where(OutboxRow.kind == "checkpoint_cleanup")))
        assert row.result_ref is None and row.disposition == "archived"
        assert len(deliveries) == 2
        assert all(delivery.available_at >= row.lease_expires_at for delivery in deliveries)
    # Repeated delivery removes both immutable keys, including the fenced late bytes.
    for delivery in deliveries:
        for key in delivery.payload["keys"]:
            await service.runtime.objects.delete(key)
    assert await service.runtime.objects.get(compactions.key(lease.workspace_id, owner.operation_id, "result")) is None


async def test_compaction_payload_owns_hydrated_media_after_run_objects_are_deleted(service, scripted_model, runs_kit):
    from a13n_harness import HarnessState
    from a13n_harness.storage import RunStorage
    from a13n_service.runs.checkpoints import RunObjects
    from pydantic_ai.messages import BinaryContent

    lease = await setup(service, scripted_model, runs_kit)
    storage = RunStorage(RunObjects(service.runtime, lease, AttemptControl()))
    data = b"large-image" * service.runtime.settings.worker.content_bytes
    original = HarnessState.new(
        thread_id=lease.thread_id,
        message_history=[
            ModelRequest(parts=[UserPromptPart(["Keep this image", BinaryContent(data, media_type="image/png")])]),
            ModelResponse(parts=[TextPart("Seen")]),
        ],
    )
    saved = await storage.export(original)
    assert saved.refs
    # Harness resolves stored messages before its compaction boundary; retained
    # steering context uses the same RunStorage resolution.
    hydrated = await storage.resolve_messages(saved.message_history)
    input = CompactionInput.create(
        operation_id="compaction-media",
        thread_id=lease.thread_id,
        run_id="harness-run",
        model_name="scripted",
        provider_name="openai",
        messages=hydrated,
        retained_requests=[hydrated[0]],
        parameters=ModelRequestParameters(),
    )
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    await compactions.publish(
        service.runtime, owner, CompactionResult.from_summary(input.basis, "An image was provided")
    )
    for ref in saved.refs:
        await service.runtime.objects.delete(ref.key)
    observed = await compactions.observe(service.runtime, lease.thread_id)
    operation = await compactions.load(service.runtime, observed)
    assert "a13n.stored" not in operation.model_dump_json()
    assert operation.input.request_messages[0].parts[0].content[1].data == data
    assert operation.result.basis.replay_requests[0].parts[0].content[1].data == data


async def test_publication_rechecks_local_deadline_after_database_wait(service, scripted_model, runs_kit, monkeypatch):
    from types import SimpleNamespace

    lease = await setup(service, scripted_model, runs_kit)
    input = frozen(lease)
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    ticks = iter([0, service.runtime.settings.worker.lease_seconds + 1])
    monkeypatch.setattr(compactions, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    async def forbidden_write(*args, **kwargs):
        pytest.fail("An expired owner started object I/O")

    monkeypatch.setattr(service.runtime.objects, "put", forbidden_write)
    with pytest.raises(LeaseLost):
        await compactions.publish(service.runtime, owner, CompactionResult.from_summary(input.basis, "late"))


async def test_detached_model_preserves_run_network_restrictions(service, scripted_model, runs_kit, caplog):
    from dataclasses import replace
    from urllib.parse import urlsplit

    from a13n_harness import RunConfiguration
    from pydantic_ai.messages import ImageUrl

    lease = await setup(service, scripted_model, runs_kit)
    plan = await _plan(service.runtime, lease)
    plan = replace(plan, configuration=RunConfiguration(allowed_hosts={urlsplit(scripted_model.url).hostname}))
    attempt = _Attempt(service.runtime, lease, AttemptControl(), plan)
    input = CompactionInput.create(
        operation_id="restricted-compaction",
        thread_id=lease.thread_id,
        run_id="harness-run",
        model_id=plan.agent.model.key,
        model_name="scripted",
        provider_name="openai",
        messages=[
            ModelRequest(parts=[UserPromptPart(["Earlier image", ImageUrl("https://denied.invalid/image.png")])]),
            ModelResponse(parts=[TextPart("Earlier answer")]),
        ],
        parameters=ModelRequestParameters(),
    )
    assert attempt.compaction is not None
    await attempt.compaction.submit(input)
    async with asyncio.timeout(10):
        operation = await attempt.compaction.wait(lease.thread_id, input.basis.operation_id)
    assert operation.status == "failed"
    assert scripted_model.requests.empty()
    assert "HostNotAllowedError" in str([record.__dict__.get("exception_details") for record in caplog.records])


@pytest.mark.parametrize("typed", [False, True], ids=["unexpected", "harness"])
async def test_generation_failure_preserves_harness_code(service, scripted_model, runs_kit, monkeypatch, typed):
    from a13n_harness import HarnessError
    from a13n_service.runs import compaction_model

    lease = await setup(service, scripted_model, runs_kit)
    plan = await _plan(service.runtime, lease)
    attempt = _Attempt(service.runtime, lease, AttemptControl(), plan)
    input = frozen(lease, model_id=plan.agent.model.key)

    async def fail_generation(*args):
        if typed:
            raise HarnessError("No summary produced", code="compaction_empty")
        raise ValueError("Unexpected provider failure")

    monkeypatch.setattr(compaction_model, "generate_compaction", fail_generation)
    assert attempt.compaction is not None
    await attempt.compaction.submit(input)
    async with asyncio.timeout(10):
        operation = await attempt.compaction.wait(lease.thread_id, input.basis.operation_id)
    expected = "compaction_empty" if typed else "compaction_failed"
    assert (operation.status, operation.error_code) == ("failed", expected)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(CompactionRow, input.basis.operation_id)
        assert row.error_code == expected


@pytest.fixture
async def small_object_executing(settings, service_app):
    from .conftest import _skill_service

    settings = settings.model_copy(update={"objects": settings.objects.model_copy(update={"max_bytes": 65536})})
    async with _skill_service(settings, service_app, "all") as service:
        yield service


async def test_oversized_frozen_input_uses_synchronous_fallback(
    small_object_executing, scripted_model, runs_kit, monkeypatch
):
    service = small_object_executing

    async def count_tokens(self, messages, model_settings, model_request_parameters):
        return RequestUsage(input_tokens=100)

    monkeypatch.setattr(OpenAIChatModel, "count_tokens", count_tokens)
    # Checkpoints compress/externalize history; frozen background inputs have a separate object bound.
    agent = await runs_kit.create_agent(
        service,
        scripted_model,
        background_compaction={"enabled": True},
        model_settings={"max_tokens": 100},
        model_characteristics={"context_window_tokens": 10000, "compact_threshold": 0.001},
    )
    scripted_model.say("Earlier answer " * 3000)
    first = await runs_kit.start_thread(service, agent, "History " * 5000)
    assert (await runs_kit.sealed(service, first["run"]["id"]))["status"] == "completed"
    await scripted_model.request()
    scripted_model.say("Synchronous summary")
    scripted_model.say("Final answer")
    response = await runs_kit.submit(service, first["thread"]["id"], runs_kit.message(agent, "Continue now"))
    assert response.status_code == 201, response.text
    completed = await runs_kit.sealed(service, response.json()["run"]["id"])
    assert completed["status"] == "completed", completed
    assert completed["output"] == "Final answer"
    await scripted_model.request()
    request = await scripted_model.request()
    assert "Synchronous summary" in json.dumps(request["messages"])
    async with transaction(service.runtime.storage) as session:
        assert await session.scalar(select(CompactionRow.id)) is None


@pytest.mark.parametrize("enabled", [False, True])
async def test_agent_policy_selects_background_generation_and_preserves_ready_delivery(
    service, scripted_model, runs_kit, enabled
):
    from a13n_service.resources.agents.schemas import AgentConfig

    assert AgentConfig(model="test").background_compaction.enabled is False
    lease = await setup(service, scripted_model, runs_kit, background=enabled)
    plan = await _plan(service.runtime, lease)
    assert plan.background_compaction is enabled
    assert (_Attempt(service.runtime, lease, AttemptControl(), plan).compaction is not None) is enabled
    input = frozen(lease)
    owner = await compactions.reserve(service.runtime, lease, input)
    assert owner is not None
    await compactions.publish(service.runtime, owner, input)
    result = CompactionResult.from_summary(input.basis, "Retained summary")
    await compactions.publish(service.runtime, owner, result)
    refreshed = await _plan(service.runtime, lease)
    assert refreshed.compaction_result == result
