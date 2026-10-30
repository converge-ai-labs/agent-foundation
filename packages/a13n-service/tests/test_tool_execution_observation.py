"""Human observations are bounded, read-only, and independent of Agent delivery."""

from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.tool_execution import StoredToolExecution
from a13n_harness.toolsets.shell_control import ShellControl
from a13n_service.infra.db import transaction
from a13n_service.runs.tables import ToolExecutionRow
from a13n_service.runs.tool_executions.output import Snapshot
from a13n_service.runs.tool_executions.store import Store

from .test_tool_execution_output import _observation
from .test_tool_execution_store import _deliver, _origin, _record, _save

pytestmark = pytest.mark.anyio


async def test_human_reads_preserve_delivery_and_retention(service, scripted_model, runs_kit):
    _, lease = await _origin(service, scripted_model, runs_kit)
    store = Store(service.runtime)
    record = _record(lease).model_copy(update={"tool_name": "shell_exec", "foreground_released": True})
    await _save(store, lease, StoredToolExecution(record))
    shell = _observation().read(record.execution_id, ShellControl(operation="wait", timeout_seconds=0))
    shell["stdout"].update(text="界" * 12000, content_complete=True, produced_bytes=36000)
    shell["status"].update(phase="exited", exit_code=0, cleanup="complete")
    complete = record.model_copy(
        update={"status": "succeeded", "completed_at": datetime.now(UTC), "result_available": True}
    )
    await _save(store, lease, StoredToolExecution(complete, shell))
    async with transaction(service.runtime.storage) as session:
        deadline = (await session.get_one(ToolExecutionRow, record.execution_id)).expires_at
    base = f"{service.api}/threads/{lease.thread_id}/tool-executions/{record.execution_id}"
    for _ in range(2):
        observed = await service.client.get(base + "/observation")
        assert observed.status_code == 200, observed.text
        value = observed.json()
        assert value["source"] == "retained"
        assert value["shell"]["stdout"]["text"] == "界" * 8000
        assert value["shell"]["stdout"]["truncated"]
        assert value["shell"]["status"]["exit_code"] == 0
        assert (await service.client.get(base + "/result")).status_code == 200
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ToolExecutionRow, record.execution_id)
        assert row.first_delivered_at is None
        assert row.expires_at == deadline
    assert len(await _deliver(service.runtime)) == 1
    assert (await store.get(lease.thread_id, record.execution_id)).record.delivery_acknowledged
    assert (await runs_kit.inbox(service, lease.thread_id))[-1]["kind"] == "tool_result"


async def test_live_observation_cache_miss_owner_loss_and_terminal_race(service, scripted_model, runs_kit, monkeypatch):
    _, lease = await _origin(service, scripted_model, runs_kit)
    store, record = Store(service.runtime), _record(lease)
    await _save(store, lease, StoredToolExecution(record))
    output = service.runtime.executions.output
    base = f"{service.api}/threads/{lease.thread_id}/tool-executions/{record.execution_id}/observation"
    assert (await service.client.get(base)).json()["source"] == "unavailable"
    output.offer(
        record.execution_id,
        Snapshot(thread_id=lease.thread_id, worker_id=lease.worker_id, observation=_observation(b"live\xff")),
    )
    await output.flush()
    response = await service.client.get(base)
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "live"
    assert response.json()["shell"]["stdout"]["text"] == "live\ufffd"
    assert not response.json()["shell"]["stdout"]["truncated"]
    assert not response.json()["shell"]["stdout"]["observation_closed"]
    assert not response.json()["execution"]["delivery_acknowledged"]
    output.offer(
        record.execution_id,
        Snapshot(
            thread_id=lease.thread_id, worker_id=lease.worker_id, observation=_observation(b"x" * 40000 + b"LATEST")
        ),
    )
    await output.flush()
    tail = (await service.client.get(base)).json()["shell"]["stdout"]
    assert tail["text"].endswith("LATEST")
    assert len(tail["text"]) == 8000
    assert tail["truncated"]
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ToolExecutionRow, record.execution_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await service.client.get(base)).json()["source"] == "unavailable"
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ToolExecutionRow, record.execution_id)
        row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
    get = output.get

    async def finish_during_read(*args):
        cached = await get(*args)
        complete = record.model_copy(update={"status": "cancelled", "completed_at": datetime.now(UTC)})
        await _save(store, lease, StoredToolExecution(complete))
        return cached

    monkeypatch.setattr(output, "get", finish_during_read)
    value = (await service.client.get(base)).json()
    assert value["source"] == "retained"
    assert value["execution"]["status"] == "cancelled"
    assert value["shell"] is None
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ToolExecutionRow, record.execution_id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await service.client.get(base)).status_code == 404


async def test_observation_exact_thread_and_workspace_scope(service, scripted_model, runs_kit):
    started, lease = await _origin(service, scripted_model, runs_kit)
    record = _record(lease)
    await _save(Store(service.runtime), lease, StoredToolExecution(record))
    other = await runs_kit.start_thread(service, {"id": started["run"]["agent_id"]}, "unrelated")
    base = f"{service.api}/threads/{lease.thread_id}/tool-executions/{record.execution_id}/observation"
    assert (await service.client.get(base, headers={"X-Workspace-ID": "ws_" + "f" * 20})).status_code in {403, 404}
    assert (await service.client.get(base.replace(lease.thread_id, other["thread"]["id"]))).status_code == 404
