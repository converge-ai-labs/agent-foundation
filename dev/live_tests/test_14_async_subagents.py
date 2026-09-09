"""Case 14: deterministic result arrival at every parent Run status."""

import logging
import signal

import pytest

from .client import agent_input
from .management_support import ManagementJourney, client_tool

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "parent_status", ["running", "completed", "accepted", "approval", "client_tool", "failed", "cancelled"]
)
async def test_async_children_result_delivery_by_parent_status(round_two, parent_status):
    lab, live = round_two, round_two.client
    journey = ManagementJourney(lab)
    case = await live.case("async_children")
    root = lab.root / "workspace" / case["case_id"]
    (root / "parent_mode").write_text(parent_status)
    options = {}
    if parent_status == "approval":
        options["plugins"] = [
            {
                "instance_name": "approval",
                "plugin_key": "live.approval",
                "config": {"root": live.config["workspace_root"]},
            }
        ]
    elif parent_status == "client_tool":
        options["client_tools"] = [client_tool()]
    agent = await journey.agent(
        subagent_mode="async", subagents={"child": {"agent_id": live.config["child_agent_id"]}}, **options
    )
    parent = await journey.start(case, agent_id=agent["agent"]["id"])
    # Only the original Worker can claim the parent. Its two children queue until
    # the parent has finished spawning and entered its independent model barrier.
    await lab.wait_evidence(case, "parent_ready", run_id=parent["run_id"])
    lab.worker_environment["A13N_SERVICE_WORKER_DRAIN_SECONDS"] = "120"
    await lab.start_worker()
    await lab.start_worker()
    for field in ("child_0_started", "child_1_started"):
        await lab.wait_evidence(case, field)
    threads = await live.collection(f"/api/v1/sessions/{parent['session_id']}/threads")
    children = [thread for thread in threads if thread["origin_run_id"] == parent["run_id"]]
    assert len(children) == 2 and all(thread["origin_thread_id"] == parent["thread_id"] for thread in children)
    child_ids = {thread["current_run_id"] for thread in children}
    assert len(child_ids) == 2
    live.runs.extend(child_ids)

    async def parent_runs():
        return await live.collection(f"/api/v1/threads/{parent['thread_id']}/runs")

    async def inbox():
        return (await live.request("GET", f"/__live__/threads/{parent['thread_id']}/inbox"))["items"]

    assert await inbox() == []
    destination = parent["run_id"]
    waiting = parent_status in {"approval", "client_tool"}
    suppressed = parent_status in {"failed", "cancelled"}
    sealed = None
    if parent_status == "cancelled":
        await live.interrupt(parent["run_id"])
        sealed = await live.finish(parent["run_id"], "cancelled")
    elif parent_status != "running":
        (root / "parent_release").touch()
        outcome = "waiting" if waiting else "failed" if suppressed else "completed"
        sealed = await live.finish(parent["run_id"], outcome)
    if waiting:
        assert sealed["wait_reason"] == parent_status
        pending_path = f"/api/v1/runs/{parent['run_id']}/pending-actions"
        pending_actions = await live.request("GET", pending_path)
        assert len(pending_actions["items"]) == 1
        assert not (await live.evidence(case))["output"]
    if parent_status == "accepted":
        # Drain both child Workers before releasing their requests: they finish
        # their existing children but cannot claim the accepted continuation.
        await lab.stop(lab.workers[0])
        for worker in lab.workers[1:]:
            lab.send(worker, signal.SIGTERM)
            await lab.wait_unready(worker)
        thread = await live.thread(parent["thread_id"])
        successor = await journey.post(
            f"/api/v1/runs/{parent['run_id']}/continue",
            {"expected_thread_version": thread["version"], "input": agent_input("Incorporate later child results.")},
            expected=202,
        )
        live.track(successor)
        destination = successor["run_id"]
        assert (await live.run(destination))["status"] == "accepted"
    expected_status = "waiting" if waiting else parent_status
    assert (await live.run(destination))["status"] == expected_status
    for child_id in child_ids:
        assert (await live.run(child_id))["status"] == "running", "Independent children must survive parent settlement"
    logger.info(
        "releasing children: parent_status=%s origin=%s destination=%s", expected_status, parent["run_id"], destination
    )
    (root / "children_release").touch()
    results = [await live.finish(child_id) for child_id in child_ids]
    assert {run["output_text"] for run in results} == {f"CHILD_RESULT_{index}_{case['token']}" for index in (0, 1)}
    rows = await live.wait(inbox, lambda rows: len(rows) == 2, "both child results durably published")
    assert {row["payload"]["child_run_id"] for row in rows} == child_ids
    assert all(row["origin_run_id"] == parent["run_id"] for row in rows)

    if suppressed:
        assert all(row["status"] == "suppressed" and row["consumed_by_run_id"] is None for row in rows)
        await live.assert_stable(parent_runs, [sealed], seconds=4)
        assert not any((await live.evidence(case))["result_deliveries"])
    else:
        if parent_status in {"running", "accepted"}:
            assert (await live.run(destination))["status"] == parent_status
            assert all(row["status"] == "pending" and row["target_run_id"] == destination for row in rows)
            if parent_status == "running":
                (root / "parent_release").touch()
            else:
                await lab.start_worker()
        elif waiting:
            assert all(
                row["status"] == "pending"
                and row["source_waiting_run_id"] == parent["run_id"]
                and row["target_run_id"] is None
                for row in rows
            )
            await live.assert_stable(parent_runs, [sealed], seconds=2)
            assert await live.request("GET", pending_path) == pending_actions
            assert not any((await live.evidence(case))["result_deliveries"])
            before_feedback = len((await live.evidence(case))["result_deliveries"])
            action = {
                "call_id": pending_actions["items"][0]["call_id"],
                "action": "approve" if parent_status == "approval" else "complete",
            }
            if parent_status == "client_tool":
                action["result"] = {"value": "explicit-client-feedback"}
            thread = await live.thread(parent["thread_id"])
            successor = await journey.post(
                f"/api/v1/runs/{parent['run_id']}/feedback",
                {
                    "expected_thread_version": thread["version"],
                    "sealed_state_digest_sha256": sealed["sealed_state_digest_sha256"],
                    "resolutions": [action],
                },
                expected=202,
            )
            live.track(successor)
            destination = successor["run_id"]
        runs = await live.wait(
            parent_runs,
            lambda runs: any(
                run["status"] == "completed" and run["output_text"] == "children-seen:0,1" for run in runs
            ),
            "both child results incorporated",
        )
        for run in runs:
            live.track({"run_id": run["id"], "thread_id": run["thread_id"]})
            if run["status"] in {"accepted", "running"}:
                await live.finish(run["id"])
        rows = await live.wait(
            inbox, lambda rows: all(row["status"] == "consumed" for row in rows), "durable child consumption"
        )
        assert all(row["consumed_state_digest_sha256"] and row["consumed_checkpoint_seq"] is not None for row in rows)
        if parent_status != "completed":
            assert {row["consumed_by_run_id"] for row in rows} == {destination}
            assert len(runs) == (1 if parent_status == "running" else 2)
        else:
            successors = [run for run in runs if run["id"] != parent["run_id"]]
            assert 1 <= len(successors) <= 2
            assert all(run["input_kind"] == "async_subagent_result" for run in successors)
            assert len({run["input"]["child_run_id"] for run in successors}) == len(successors)
        observed = (await live.evidence(case))["result_deliveries"]
        assert {child for request in observed for child in request} == child_ids
        assert all(len(request) == len(set(request)) for request in observed), "Duplicate result incorporation"
        if waiting:
            assert observed[before_feedback] == [], "Waiting-bound results leaked into the first feedback model request"
        await live.assert_stable(parent_runs, await parent_runs(), seconds=4)
    await live.assert_stable(inbox, rows, seconds=2)
    if sealed:
        assert await live.run(parent["run_id"]) == sealed
    for result in results:
        assert await live.run(result["id"]) == result
    final_threads = await live.collection(f"/api/v1/sessions/{parent['session_id']}/threads")
    assert len([thread for thread in final_threads if thread["origin_run_id"] == parent["run_id"]]) == 2
