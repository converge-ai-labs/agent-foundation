"""Case 2: reconstruct context into a new Run without rewriting its parent."""

from uuid import uuid4

import pytest

from .client import agent_input

pytestmark = pytest.mark.anyio


async def test_continuation_preserves_context(live):
    case = await live.case("remember")
    first = await live.start(case)
    parent = await live.finish(first["run_id"])
    assert parent["output_text"] == "remembered"
    thread = await live.thread(first["thread_id"])
    # The second request deliberately contains neither the token nor the scenario JSON.
    second = await live.request(
        "POST",
        f"/api/v1/runs/{parent['id']}/continue",
        expected=202,
        headers={"Idempotency-Key": uuid4().hex},
        json={"expected_thread_version": thread["version"], "input": agent_input("Recall the remembered token.")},
    )
    live.track(second)
    child = await live.finish(second["run_id"])
    assert child["output_text"] == case["token"]
    assert child["id"] != parent["id"]
    assert child["parent_run_id"] == parent["id"]
    assert child["thread_id"] == parent["thread_id"]
    assert child["session_id"] == parent["session_id"]
    assert await live.run(parent["id"]) == parent
    assert (await live.thread(child["thread_id"]))["head_run_id"] == child["id"]
