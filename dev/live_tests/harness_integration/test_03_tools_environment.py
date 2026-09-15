"""Case 3: the Worker executes a real shell command in its selected Environment."""

import pytest

from ..protocol.stream import assert_stream

pytestmark = pytest.mark.anyio


async def test_environment_tool_writes_and_reads_file(live):
    case = await live.case("tools")
    receipt = await live.start(case)
    run = await live.finish(receipt["run_id"])
    assert run["environment_id"] == live.config["environment_id"]
    evidence = await live.evidence(case)
    assert evidence["output"] == case["token"], "No matching file was written by the tool"
    assert evidence["shell_read"] == case["token"], "The shell did not read back the written file"
    events = await live.events(run["id"])
    assert_stream(events, run["id"])
    assert any(event.kind == "agui.tool_call_start" for event in events)
    assert any(event.kind == "agui.tool_call_result" for event in events)
    items = await live.retained_items(run["id"])
    assert any(item["kind"] == "tool_call" and item["state"] == "completed" for item in items)
