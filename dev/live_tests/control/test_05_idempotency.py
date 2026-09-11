"""Case 5: concurrent identical submissions and changed-intent conflicts."""

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
    assert receipts[0] == receipts[1]
    run = await live.finish(receipts[0]["run_id"])
    assert await live.start(case, key=key) == receipts[0], "Replay changed after completion"
    await live.request(
        "POST",
        f"/api/v1/workspaces/{live.config['workspace_id']}/runs",
        expected=409,
        headers={"Idempotency-Key": key},
        json=live.start_body({**case, "token": uuid4().hex}),
    )
    runs = await live.collection(f"/api/v1/threads/{run['thread_id']}/runs")
    assert [item["id"] for item in runs] == [run["id"]]
    assert len(await live.collection(f"/api/v1/runs/{run['id']}/attempts")) == 1
    assert (await live.evidence(case))["model_requests"] == 1
