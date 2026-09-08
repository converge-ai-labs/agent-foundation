"""Case 14: durable child correlation, result delivery and independent cancellation policy."""

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cancel_parent", [False, True])
async def test_async_children_deliver_once_or_are_suppressed_after_parent_cancel(round_two, cancel_parent):
    lab, live = round_two, round_two.client
    # One parent and two blocked children need three real execution slots.
    await lab.start_worker()
    await lab.start_worker()
    case = await live.case("async_children")
    body = {**live.start_body(case), "agent_id": live.config["async_agent_id"]}
    parent = await live.request(
        "POST",
        f"/api/v1/workspaces/{live.config['workspace_id']}/runs",
        expected=202,
        headers={"Idempotency-Key": case["case_id"]},
        json=body,
    )
    live.track(parent)
    for field in ("parent_ready", "child_0_started", "child_1_started"):
        await lab.wait_evidence(case, field, run_id=parent["run_id"])
    threads = await live.collection(f"/api/v1/sessions/{parent['session_id']}/threads")
    children = [thread for thread in threads if thread["origin_run_id"] == parent["run_id"]]
    assert len(children) == 2
    assert all(thread["origin_thread_id"] == parent["thread_id"] for thread in children)
    child_ids = [thread["current_run_id"] for thread in children]
    assert len(set(child_ids)) == 2
    live.runs.extend(child_ids)
    if cancel_parent:
        await live.interrupt(parent["run_id"])
        cancelled = await live.finish(parent["run_id"], "cancelled")
        for run_id in child_ids:
            assert (await live.run(run_id))["status"] == "running", "Default independent children were cancelled"
    await live.release(case)
    results = [await live.finish(run_id) for run_id in child_ids]
    assert {run["output_text"] for run in results} == {f"CHILD_RESULT_{index}_{case['token']}" for index in (0, 1)}

    async def parent_runs():
        return await live.collection(f"/api/v1/threads/{parent['thread_id']}/runs")

    if cancel_parent:
        await live.assert_stable(parent_runs, [cancelled], seconds=4)
    else:
        await live.finish(parent["run_id"])
        runs = await live.wait(
            parent_runs,
            lambda values: any(run["output_text"] == "children-seen:0,1" for run in values),
            "both child results incorporated",
        )
        # An inactive parent may need one or two ordinary successor Runs; each input identifies one result.
        for run in runs:
            if run["id"] not in live.runs:
                live.runs.append(run["id"])
                await live.finish(run["id"])
        deliveries = [run["input"] for run in runs if run["input_kind"] == "async_subagent_result"]
        assert len({value["child_run_id"] for value in deliveries}) == len(deliveries)
        await live.assert_stable(parent_runs, await parent_runs(), seconds=4)
        observed = (await live.evidence(case))["result_deliveries"]
        assert {child for request in observed for child in request} == set(child_ids)
        assert all(len(request) == len(set(request)) for request in observed), "Duplicate result incorporation"
    final_threads = await live.collection(f"/api/v1/sessions/{parent['session_id']}/threads")
    assert len([thread for thread in final_threads if thread["origin_run_id"] == parent["run_id"]]) == 2
    for result in results:
        assert await live.run(result["id"]) == result
