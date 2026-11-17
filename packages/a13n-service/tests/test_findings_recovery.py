"""Managed inline Analysis recovery retains native results in its single accepted Run."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from a13n_harness import HarnessState
from a13n_harness.tool_execution import StoredToolExecution
from a13n_service.infra import outbox
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.findings import analysis, preset
from a13n_service.runs.findings.schemas import AnalysisCreate
from a13n_service.runs.findings.tables import AnalysisRow
from a13n_service.runs.seal import LeaseExpirer
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow, ToolExecutionRow
from a13n_service.runs.tool_executions.delivery import tool_results
from a13n_service.runs.tool_executions.recovery import acknowledge, restore
from a13n_service.runs.tool_executions.store import Store, managed_inline
from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.messages import (
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import func, select, update

from .test_findings import TRACE, Backend, actor, finding, target
from .test_tool_execution_store import _record, _save

pytestmark = pytest.mark.anyio


async def origin(service, scripted_model, runs_kit, *, mode="inline"):
    await runs_kit.pause_sweeps(service)
    agent, run = await target(service, scripted_model, runs_kit)
    runtime = replace(service.runtime, traces=Backend(service, run["id"]))
    service.app.state.runtime = runtime
    configuration = preset.configuration

    def configured(model):
        current = configuration(model).model_dump(mode="json")
        current["execution"]["tools"]["mode"] = mode
        return type(configuration(model)).model_validate(current)

    # Historical auto analyses must be accepted with that policy, never mutate a frozen Run.
    with patch.object(preset, "configuration", configured):
        created, _ = await analysis.start(
            runtime, actor(service), service.tenant.workspace_id, AnalysisCreate(trace_id=TRACE), request_key="recovery"
        )
    (lease,) = await claim(runtime, worker_id="analysis-a", worker_build="test", limit=1)
    assert lease.run_id == created.run_id
    return runtime, created, lease, agent, run


@pytest.mark.parametrize("analysis_origin,mode", [(True, "inline"), (True, "auto"), (False, "inline"), (False, "auto")])
async def test_classification_uses_analysis_association_and_frozen_mode(
    service, scripted_model, runs_kit, analysis_origin, mode
):
    runtime, created, lease, _, _ = await origin(service, scripted_model, runs_kit, mode=mode)
    async with transaction(runtime.storage) as session:
        run = await session.get_one(RunRow, lease.run_id)
        if not analysis_origin:
            await session.delete(await session.get_one(AnalysisRow, created.id))
            await session.flush()
        assert await managed_inline(session, run) is (analysis_origin and mode == "inline")
    store, record = Store(runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    record = record.model_copy(
        update={"status": "succeeded", "result_available": True, "completed_at": datetime.now(UTC)}
    )
    await _save(store, lease, StoredToolExecution(record, {"native": True}))
    await store.acknowledge_delivery(lease.thread_id, record.execution_id)
    assert (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged is not (
        analysis_origin and mode == "inline"
    )
    state = HarnessState.new(
        thread_id=lease.thread_id,
        message_history=[ModelResponse(parts=[ToolCallPart("slow_tool", {}, "call-original")])],
    )
    restored = await restore(runtime, state, thread_id=lease.thread_id, origin_run_id=lease.run_id)
    content = restored.message_history[-1].parts[0].content
    assert content == ({"native": True} if analysis_origin and mode == "inline" else record.receipt())


@pytest.mark.parametrize("value", ["plain", "null", "native", "retry", "failed", "cancelled", "cancelled_default"])
async def test_native_result_restoration_and_checkpoint_ack(service, scripted_model, runs_kit, value):
    runtime, created, lease, _, _ = await origin(service, scripted_model, runs_kit)
    store, record = Store(runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    failed = value in {"failed", "cancelled", "cancelled_default"}
    status = "failed" if value in {"failed", "retry"} else "cancelled" if failed else "succeeded"
    error = "The analysis must read a trace before citing it." if value == "failed" else "Stopped by the operator."
    record = record.model_copy(
        update={
            "status": status,
            "error": error if failed and value != "cancelled_default" else None,
            "result_available": not failed and value != "retry",
            "completed_at": datetime.now(UTC),
            "foreground_released": True,
        }
    )
    result = (
        {"ok": True}
        if value == "plain"
        else ToolReturn(
            return_value={"ok": True},
            content=[BinaryContent(b"image", media_type="image/png")],
            metadata={"saved": True},
        )
        if value == "native"
        else None
    )
    await _save(
        store,
        lease,
        StoredToolExecution(record, result, retry_prompt="Correct the argument" if value == "retry" else None),
    )
    (claimed,) = await outbox.claim(
        runtime.storage, "tool_result", owner="old", limit=1, lease_seconds=60, max_attempts=12
    )
    state = HarnessState.new(
        thread_id=lease.thread_id,
        message_history=[ModelResponse(parts=[ToolCallPart("slow_tool", {}, "call-original")])],
    )
    restored = await restore(runtime, state, thread_id=lease.thread_id, origin_run_id=lease.run_id)
    returned = restored.message_history[-1].parts[0]
    if failed:
        assert isinstance(returned, ToolReturnPart) and returned.outcome == "failed"
        assert returned.content == (error if value != "cancelled_default" else "Tool execution cancelled.")
    elif value == "retry":
        assert isinstance(returned, ToolReturnPart) and returned.outcome == "retried"
        assert returned.content == "Correct the argument"
    else:
        assert isinstance(returned, ToolReturnPart)
        assert returned.content == (None if value == "null" else {"ok": True})
        if value == "native":
            assert returned.metadata == {"saved": True}
            assert isinstance(restored.message_history[-1].parts[1], UserPromptPart)
            assert restored.message_history[-1].parts[1].content[0].data == b"image"
    # Reading is not consumption. Another interruption can restore identical native content.
    assert not (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged
    assert (await restore(runtime, state, thread_id=lease.thread_id, origin_run_id=lease.run_id)).message_history[
        -1
    ].parts[0].content == returned.content
    async with transaction(runtime.storage) as session:
        row = await session.get_one(RunRow, lease.run_id)
        await acknowledge(session, row, restored, at=await now(session), seconds=60)
        await session.rollback()
    assert not (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged
    await tool_results(runtime)(claimed)
    async with transaction(runtime.storage) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(InboxEntryRow).where(InboxEntryRow.kind == "tool_result")
            )
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.thread_id == created.thread_id))
            == 1
        )
        row = await session.get_one(RunRow, lease.run_id)
        await acknowledge(session, row, restored, at=await now(session), seconds=60)
    assert (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged


@pytest.mark.parametrize("status", ["running", "lost", "expired", "missing", "corrupt"])
async def test_unavailable_admitted_result_never_becomes_a_replayed_call(service, scripted_model, runs_kit, status):
    runtime, _, lease, _, _ = await origin(service, scripted_model, runs_kit)
    store, record = Store(runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    if status != "running":
        record = record.model_copy(
            update={
                "status": "lost" if status == "lost" else "succeeded",
                "result_available": status != "lost",
                "completed_at": datetime.now(UTC),
            }
        )
        await _save(store, lease, StoredToolExecution(record, "result"))
        async with transaction(runtime.storage) as session:
            row = await session.get_one(ToolExecutionRow, record.execution_id)
            ref = row.result_ref
            if status == "expired":
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        if status == "missing":
            await runtime.objects.delete(ref["key"])
        if status == "corrupt":
            await runtime.objects.put(ref["key"], b"corrupt", content_type="application/json")
    state = HarnessState.new(
        thread_id=lease.thread_id,
        message_history=[ModelResponse(parts=[ToolCallPart("slow_tool", {}, "call-original")])],
    )
    with pytest.raises(ServiceError) as error:
        await restore(runtime, state, thread_id=lease.thread_id, origin_run_id=lease.run_id)
    assert error.value.code == ("unavailable" if status == "running" else "conflict")
    if status != "running":
        assert error.value.details["reason"] == "tool_result_unavailable"
    assert (
        not (await Store(runtime).get(lease.thread_id, record.execution_id)).record.foreground_released
        if status in {"running", "lost"}
        else True
    )


@pytest.mark.parametrize("accepted", [True, False])
async def test_takeover_keeps_tool_outcome_and_completion_in_one_run(
    service, scripted_model, runs_kit, monkeypatch, accepted
):
    from a13n_service.runs.findings import service as findings

    runtime, created, lease, agent, run = await origin(service, scripted_model, runs_kit)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    foreground_acks = 0
    create = findings.create_finding
    acknowledge_delivery = Store.acknowledge_delivery

    async def foreground_ack(store, thread_id, execution_id):
        nonlocal foreground_acks
        await acknowledge_delivery(store, thread_id, execution_id)
        async with transaction(runtime.storage) as session:
            row = await session.get_one(ToolExecutionRow, execution_id)
            # Native foreground reconciliation during export_state precedes the
            # checkpoint transaction, so it must not start delivered retention.
            assert row.first_delivered_at is None
        foreground_acks += 1

    monkeypatch.setattr(Store, "acknowledge_delivery", foreground_ack)

    async def paused(*args, **kwargs):
        nonlocal calls
        calls += 1
        try:
            return await create(*args, **kwargs)
        finally:
            entered.set()
            await finish.wait()

    monkeypatch.setattr(findings, "create_finding", paused)
    scripted_model.call("get_trace", {"trace_id": TRACE}, call_id="read")
    submitted = finding(agent, run)
    if not accepted:
        submitted["evidence"][0]["trace_id"] = "c" * 32
    scripted_model.call("create_finding", submitted, call_id="write")
    scripted_model.call("get_trace", {"trace_id": TRACE}, call_id="continue")
    scripted_model.say("Completed original analysis")
    async with runs_kit.platform_runtime(service, runtime) as served:
        running = asyncio.create_task(execute(served, lease, AttemptControl()))
        try:
            await asyncio.wait_for(entered.wait(), 20)
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            finish.set()
            await asyncio.wait_for(served.executions.join(), 10)
            async with transaction(runtime.storage) as session:
                await session.execute(
                    update(AttemptRow)
                    .where(AttemptRow.id == lease.attempt_id)
                    .values(lease_expires_at=AttemptRow.created_at)
                )
            await LeaseExpirer(served, batch=10)()
            async with transaction(runtime.storage) as session:
                await session.execute(
                    update(RunRow).where(RunRow.id == lease.run_id).values(available_at=RunRow.created_at)
                )
            (next_lease,) = await claim(served, worker_id="analysis-b", worker_build="test", limit=1)
            assert next_lease.run_id == created.run_id
            await execute(served, next_lease, AttemptControl())
            result = await runs_kit.get_run(service, created.run_id)
            assert result["status"] == "completed" and result["attempts"] == 2, result
            assert foreground_acks > 0
            assert calls == 1
            rows = (await service.client.get("/api/v1/findings")).json()["items"]
            assert len(rows) == int(accepted)
            assert not rows or rows[0]["source_run_id"] == created.run_id
            if not accepted:
                request = None
                while not scripted_model.requests.empty():
                    request = scripted_model.requests.get_nowait()
                returned = next(message for message in request["messages"] if message.get("tool_call_id") == "write")
                assert "must read a trace" in returned["content"]
            status = (
                await analysis.list_analyses(
                    runtime, actor(service), service.tenant.workspace_id, limit=20, cursor=None
                )
            ).items[0]
            assert status.run_id == created.run_id
            assert status.finding_count == status.cited_trace_count == int(accepted)
            async with transaction(runtime.storage) as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(RunRow).where(RunRow.thread_id == created.thread_id)
                    )
                    == 1
                )
        finally:
            finish.set()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
