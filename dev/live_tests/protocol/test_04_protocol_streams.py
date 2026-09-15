"""HTTP contract journeys: spec 21/22/24 and upstream AG-UI event semantics.

Only the model provider is scripted. Acceptance, execution, event conversion,
storage, gateway projection, SSE, and replay run in real Control/Worker processes.
"""

import json
import logging

import pytest

from .hosted_client import HostedClient, message_snapshot
from .stream_contract import assert_hosted_contract, assert_native_contract

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


EVENT_SCENARIOS = [
    ("protocol_text", "completed"),
    ("protocol_tools", "completed"),
    ("model_error", "failed"),
]


async def assert_native_journey(live, case, run, events, scenario, outcome):
    sequences = assert_native_contract(events, run)
    if outcome == "completed":
        expected = ("协议🙂\n" if scenario == "protocol_text" else "") + case["token"]
        assert "".join(sequence.assistant_text() for sequence in sequences.values()) == run["output_text"] == expected
    if scenario == "protocol_text":
        assert any(event.kind == "agui.reasoning_message_content" for event in events), (
            "Reasoning fixture was not exercised"
        )
    if scenario == "protocol_tools":
        calls = [tool for sequence in sequences.values() for tool in sequence.tools.values()]
        assert len(calls) == 2 and all(tool["result"] for tool in calls)
        assert (await live.evidence(case))["effects"] == 2
    if outcome == "failed":
        assert (await live.evidence(case))["injected_errors"] == 5, (
            "Model recovery skipped or exceeded its five-attempt budget"
        )
    assert await live.events(run["id"]) == events
    assert await live.events(run["id"], after=events[-1].cursor) == []


@pytest.mark.parametrize("scenario,outcome", EVENT_SCENARIOS)
async def test_native_event_contracts(live, scenario, outcome):
    case = await live.case(scenario)
    body = {**live.start_body(case), "agent_id": live.config["protocol_agent_id"]}
    receipt = await live.request(
        "POST",
        f"/api/v1/workspaces/{live.config['workspace_id']}/runs",
        expected=202,
        headers={"Idempotency-Key": case["case_id"]},
        json=body,
    )
    live.track(receipt)
    events = await live.events(receipt["run_id"])
    run = await live.finish(receipt["run_id"], outcome)
    await assert_native_journey(live, case, run, events, scenario, outcome)
    logger.info("Native contract verified: run=%s events=%s outcome=%s", run["id"], len(events), outcome)


@pytest.mark.parametrize("scenario,outcome", EVENT_SCENARIOS)
async def test_hosted_and_native_event_contracts(live, scenario, outcome):
    case = await live.case(scenario)
    hosted = HostedClient(live, case, agent_id=live.config["protocol_agent_id"])
    frames = await hosted.events()
    run = await live.finish((await hosted.run())["id"], outcome)
    sequence = assert_hosted_contract(frames, hosted.body, outcome, private_ids=await hosted.private_ids())
    native = await live.events(run["id"])
    await assert_native_journey(live, case, run, native, scenario, outcome)
    if outcome == "completed":
        assert sequence.assistant_text() == run["output_text"]
    if scenario == "protocol_text":
        assert sum(frame.data["type"] == "TEXT_MESSAGE_CONTENT" for frame in frames) > 1
    if scenario == "protocol_tools":
        assert len(sequence.tools) == 2 and all(tool["result"] for tool in sequence.tools.values())
        for tool in sequence.tools.values():
            assert json.loads(tool["args"]) == {"case_id": case["case_id"], "token": case["token"]}
    if outcome == "failed":
        assert frames[-1].data["message"]
    # The same external id reattaches, including after the Run seals.
    assert await hosted.events() == frames
    midpoint = len(frames) // 2
    assert await hosted.events(after=frames[midpoint].cursor) == frames[midpoint + 1 :]
    assert await hosted.events(after=frames[-1].cursor) == []
    assert (await hosted.run())["id"] == run["id"]
    logger.info(
        "Protocol contract verified: run=%s outcome=%s hosted=%s native=%s",
        run["id"],
        outcome,
        len(frames),
        len(native),
    )


@pytest.mark.parametrize("cancel", [False, True], ids=["disconnect", "cancel"])
async def test_hosted_disconnect_reconnect_and_cancel(live, cancel):
    case = await live.case("interrupt_model")
    hosted = HostedClient(live, case)
    prefix = []
    async with hosted.stream() as stream:
        async for event in stream:
            prefix.append(event)
            if event.data["type"] == "RUN_STARTED":
                break
    await live.wait_evidence(case, "model_started")
    run = await hosted.run()
    assert run["status"] == "running", "Disconnect cancelled the hosted Run"
    if cancel:
        await hosted.cancel()
        await live.wait_evidence(case, "model_closed")
    await live.release(case)
    outcome = "cancelled" if cancel else "completed"
    run = await live.finish(run["id"], outcome)
    suffix = await hosted.events(after=prefix[-1].cursor)
    complete = await hosted.events()
    assert prefix + suffix == complete, "Hosted reconnect lost, duplicated, or changed events"
    assert_hosted_contract(complete, hosted.body, outcome, private_ids=await hosted.private_ids())
    assert_native_contract(await live.events(run["id"]), run)


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_hosted_waiting_and_feedback_contract(live, action):
    case = await live.case("approval")
    hosted = HostedClient(live, case, agent_id=live.config["approval_agent_id"])
    frames = await hosted.events()
    waiting = await live.finish((await hosted.run())["id"], "waiting")
    sequence = assert_hosted_contract(frames, hosted.body, "waiting", private_ids=await hosted.private_ids())
    assert_native_contract(await live.events(waiting["id"]), waiting)
    assert not (await live.evidence(case))["output"]
    pending = await live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
    assert len(pending["items"]) == 1 and pending["items"][0]["kind"] == "approval"
    # Spec 22 requires the complete authorized pending contract in the waiting projection.
    projected = frames[-1].data["value"]["pending"]
    assert projected == waiting["pending"]
    assert projected["schema_version"] == "1" and projected["resolution_policy"] == "all"
    assert [(call["call_id"], call["kind"]) for call in projected["calls"]] == [
        (call["call_id"], call["kind"]) for call in pending["items"]
    ]
    call_id = pending["items"][0]["call_id"]
    assert call_id in sequence.tools
    assert await hosted.events() == frames
    resumed = HostedClient(live, case, agent_id=hosted.agent_id, parent=waiting["id"])
    resumed.body.update(
        {
            "threadId": hosted.body["threadId"],
            "parentRunId": hosted.body["runId"],
            "messages": hosted.body["messages"] + message_snapshot(frames),
            "forwardedProps": {"a13n": {"schema_version": "1", "resume": [{"call_id": call_id, "action": action}]}},
        }
    )
    following = await resumed.events()
    run = await live.finish((await resumed.run())["id"])
    assert run["id"] != waiting["id"] and run["thread_id"] == waiting["thread_id"]
    following_sequence = assert_hosted_contract(
        following, resumed.body, "completed", prior_tools=sequence.tools, private_ids=await resumed.private_ids()
    )
    assert following_sequence.assistant_text() == run["output_text"] == "approval-resolved"
    assert following_sequence.tools[call_id]["result"] == (action == "approve")
    assert_native_contract(await live.events(run["id"]), run, prior_tools=sequence.tools)
    assert await live.run(waiting["id"]) == waiting
    assert (await live.evidence(case))["approval_executions"] == (1 if action == "approve" else 0)


async def test_hosted_invalid_input_and_conflicting_reuse_do_not_accept_runs(live):
    case = await live.case("basic")
    hosted = HostedClient(live, case)
    path = f"/api/v1/workspaces/{live.config['workspace_id']}/runs"
    before = {run["id"] for run in await live.collection(path)}
    invalid = {**hosted.body, "messages": [{"id": "injected", "role": "assistant", "content": "untrusted history"}]}
    response = await live.http.post(hosted.path, headers={"Accept": "text/event-stream"}, json=invalid)
    assert response.status_code in {400, 422}
    assert "text/event-stream" not in response.headers.get("content-type", "")
    assert {run["id"] for run in await live.collection(path)} == before
    await hosted.events()
    run = await hosted.run()
    response = await live.http.post(
        hosted.path,
        headers={"Accept": "text/event-stream"},
        json={
            **hosted.body,
            "messages": [
                {
                    "id": "changed",
                    "role": "user",
                    "content": "different intent",
                }
            ],
        },
    )
    assert response.status_code == 409
    assert {item["id"] for item in await live.collection(path)} == before | {run["id"]}
