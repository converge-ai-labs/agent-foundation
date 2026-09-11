"""Fork and source-Thread control commands progress with their peer still paused."""

import pytest

from ..infrastructure.client import agent_input
from .control_support import assert_absent
from .fork_support import accepted_reply, assert_fork, assert_paused, completed_source, paused_command

pytestmark = pytest.mark.anyio


async def source_command(journey, source, operation):
    live = journey.live
    held_tool = None
    if operation in {"feedback", "waiting_continue"}:
        case, current = await journey.waiting(source=source)
        await journey.steer(current["id"])
    else:
        case = await journey.case(effect=operation in {"steer", "interrupt"})
        if operation == "retry":
            journey.plan(case, failure="401", failures=100)
        if operation in {"steer", "interrupt"}:
            await journey.lab.start_worker()
            held_tool = journey.arm("current-tool", "tool.after_effect", case_id=case["case_id"])
        receipt = await journey.accept(*await journey.command(source, "continue", case=case))
        if held_tool:
            await journey.reached(held_tool)
            current = await live.run(receipt["run_id"])
        else:
            current = await live.finish(receipt["run_id"], "failed" if operation == "retry" else "completed")
        if operation == "retry":
            journey.plan(case)
    if operation == "continue":
        command = await journey.command(current, "submit", case=await journey.case())
    elif operation == "continue_from":
        command = await journey.command(source, "continue", case=await journey.case())
    elif operation == "steer":
        command = f"/api/v1/runs/{current['id']}/steer", agent_input("Continue with the new direction.")
    else:
        command = await journey.command(current, operation)
    if operation in {"steer", "interrupt"}:
        point, match = "control." + operation + "_prepared", {"run_id": current["id"]}
    else:
        point, match = "control.state_published", {"thread_id": source["thread_id"]}
    return current, case, command, point, match, held_tool


async def settle_source(journey, current, receipt, operation, held_tool):
    if held_tool:
        journey.release(held_tool)
    result = await journey.live.finish(
        current["id"] if operation in {"steer", "interrupt"} else receipt["run_id"],
        "cancelled" if operation == "interrupt" else "completed",
    )
    thread = await journey.live.thread(current["thread_id"])
    assert thread["current_run_id"] == result["id"]
    if operation in {"feedback", "waiting_continue"}:
        rows = await journey.inbox(current["thread_id"])
        assert len(rows) == 1 and rows[0]["status"] == "consumed" and rows[0]["consumed_by_run_id"] == result["id"]
    if operation == "steer":
        rows = await journey.inbox(current["thread_id"])
        assert len(rows) == 1 and rows[0]["id"] == receipt["steer_id"] and rows[0]["status"] == "consumed"
    return thread


@pytest.mark.parametrize(
    "operation", ["continue", "continue_from", "steer", "interrupt", "feedback", "waiting_continue", "retry"]
)
@pytest.mark.parametrize("paused", ["fork", "source"])
async def test_fork_and_control_complete_before_the_other_command_is_released(control, operation, paused):
    journey, live = control, control.live
    source = await completed_source(journey)
    current, current_case, command, point, match, held_tool = await source_command(journey, source, operation)
    fork_case = await journey.case()
    fork_command = await journey.command(source, "fork", case=fork_case)
    if paused == "fork":
        async with paused_command(journey, *fork_command, parent_run_id=source["id"]) as (barrier, task):
            response = await journey.post(*command, expected=202)
            receipt = response.get("run", response)
            if operation not in {"steer", "interrupt"}:
                live.track(receipt)
            after = await settle_source(journey, current, receipt, operation, held_tool)
            assert_paused(barrier, task)
            journey.release(barrier)
            fork = await accepted_reply(journey, task)
            await assert_fork(journey, source, fork, fork_case)
            assert await live.thread(source["thread_id"]) == after
    else:
        async with paused_command(journey, *command, point=point, **match) as (barrier, task):
            before = await live.thread(source["thread_id"])
            inbox = await journey.inbox(source["thread_id"])
            fork = await journey.accept(*fork_command)
            await assert_fork(journey, source, fork, fork_case)
            assert_paused(barrier, task)
            assert await live.thread(source["thread_id"]) == before
            assert await journey.inbox(source["thread_id"]) == inbox
            journey.release(barrier)
            if operation in {"steer", "interrupt"}:
                reply = await task
                assert reply.status_code == 202, reply.text
                receipt = reply.json()
            else:
                receipt = await accepted_reply(journey, task)
            await settle_source(journey, current, receipt, operation, held_tool)
    fork_result = await live.run(fork["run_id"])
    assert fork_result["agent_revision_id"] == source["agent_revision_id"]
    if operation not in {"steer", "interrupt"}:
        advanced = await live.run(receipt["run_id"])
        expected_parent = source["id"] if operation in {"continue_from", "retry"} else current["id"]
        assert advanced["parent_run_id"] == expected_parent
        assert advanced["retry_of_run_id"] == (current["id"] if operation == "retry" else None)
        if operation == "retry":
            for field in ("input", "input_kind", "agent_revision_id", "effective_agent_config_digest"):
                assert advanced[field] == current[field]
            assert advanced["output_text"] == current_case["token"]
    assert_absent(journey.observations(fork_case), [current_case["case_id"]])
    assert await live.run(source["id"]) == source
    assert len(await journey.thread_runs(fork)) == 1


@pytest.mark.parametrize("paused_selection", ["inherited", "override"])
async def test_distinct_forks_sharing_an_environment_do_not_wait_for_each_other(control, paused_selection):
    journey, live = control, control.live
    environment, _ = await journey.environment()
    source = await completed_source(journey, environment={"environment_id": environment["id"]})
    before = await live.thread(source["thread_id"])
    cases = [await journey.case(), await journey.case()]
    commands = [await journey.command(source, "fork", case=case) for case in cases]
    commands[0 if paused_selection == "override" else 1][1]["config_override"] = {
        "instructions": "Fork-specific instructions"
    }
    async with paused_command(journey, *commands[0], parent_run_id=source["id"]) as (barrier, task):
        independent = await journey.accept(*commands[1])
        first = await assert_fork(journey, source, independent, cases[1])
        assert_paused(barrier, task)
        journey.release(barrier)
        delayed = await accepted_reply(journey, task)
        second = await assert_fork(journey, source, delayed, cases[0])
    assert first["thread_id"] != second["thread_id"] and first["id"] != second["id"]
    assert first["environment_id"] == second["environment_id"] == source["environment_id"]
    assert await live.thread(source["thread_id"]) == before
    assert len(await journey.runs()) == 3
