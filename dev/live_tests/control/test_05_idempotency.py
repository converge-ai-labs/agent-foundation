"""Case 5: concurrent key reuse and current-state replay without repeated execution."""

from uuid import uuid4

import anyio
import pytest

pytestmark = pytest.mark.anyio


async def test_start_is_idempotent(live):
    case = await live.case("basic")
    key = uuid4().hex
    receipts = []

    async def submit():
        receipts.append(await live.start(case, key=key))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(submit)
        tasks.start_soon(submit)
    stable_fields = ("session_id", "thread_id", "run_id", "hook_subscription_id", "status", "schema_version")
    assert {field: receipts[0][field] for field in stable_fields} == {
        field: receipts[1][field] for field in stable_fields
    }
    run = await live.finish(receipts[0]["run_id"])
    replay = await live.start(case, key=key)
    await live.assert_current_acceptance(replay, receipts[0])
    changed = await live.request(
        "POST",
        f"/api/v1/workspaces/{live.config['workspace_id']}/runs",
        expected=202,
        headers={"Idempotency-Key": key},
        json=live.start_body({**case, "token": uuid4().hex}),
    )
    assert changed == replay
    assert run["output_text"] == case["token"]
    runs = await live.collection(f"/api/v1/threads/{run['thread_id']}/runs")
    assert [item["id"] for item in runs] == [run["id"]]
    assert len(await live.collection(f"/api/v1/runs/{run['id']}/attempts")) == 1
    assert (await live.evidence(case))["model_requests"] == 1
