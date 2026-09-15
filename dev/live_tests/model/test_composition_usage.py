"""Real delegation and overlapping Runs preserve Model usage ownership."""

import asyncio
import json
from uuid import uuid4

import pytest

from .test_usage_faults import assert_usage, journey_lab

pytestmark = pytest.mark.anyio


async def inline_agent(journey):
    child = await journey.agent()
    return await journey.agent(subagent_mode="inline", subagents={"child": {"agent_id": child["agent"]["id"]}})


async def test_inline_usage_and_redelivery_charge_each_request_once(request):
    async with journey_lab(request) as journey:
        parent = await inline_agent(journey)
        case = await journey.case(inline_child=True)
        replay = journey.arm(uuid4().hex, "usage.replayed", action="observe", times=3)
        journey.arm(uuid4().hex, "usage.redeliver", action="observe", times=3, records=1)
        run = await journey.live.finish((await journey.start(case, agent_id=parent["agent"]["id"]))["run_id"])
        assert "CHILD_" + case["token"] in run["output_text"]
        assert len(journey.observations(case)) == 3
        await journey.reached(replay, hit=3)
        execution, rows = await assert_usage(journey, run, requests=3, tokens=30)
        assert len(execution["attempts"]) == 1 and len(rows) == 3
        child = [row["record"] for row in rows if row["record"]["parent_agent_instance_id"] is not None]
        roots = [row["record"] for row in rows if row["record"]["parent_agent_instance_id"] is None]
        assert len(child) == 1 and len(roots) == 2
        assert child[0]["parent_agent_instance_id"] == roots[0]["agent_instance_id"]
        assert child[0]["run_id"] != roots[0]["run_id"]


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
async def test_inline_child_cannot_bypass_parent_run_token_budget(request, field):
    async with journey_lab(request, limit={field: 10}) as journey:
        parent = await inline_agent(journey)
        case = await journey.case(inline_child=True)
        run = await journey.live.finish((await journey.start(case, agent_id=parent["agent"]["id"]))["run_id"], "failed")
        assert run["failure"]["code"] == "execution_usage_exhausted"
        assert len(journey.observations(case)) == 1, "The inline child reached the provider after budget exhaustion"
        _, rows = await assert_usage(journey, run, requests=1, tokens=10)
        assert len(rows) == 1 and rows[0]["record"]["parent_agent_instance_id"] is None


@pytest.mark.parametrize("failure", ["cancel", "budget"])
async def test_concurrent_runs_keep_receipts_budgets_and_cancellation_isolated(request, failure):
    async with journey_lab(request, limit={"input_tokens": 10, "output_tokens": 10}) as journey:
        await journey.lab.start_worker()
        good = await journey.case(effect=True, usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        bad = (
            await journey.case(failure="paused", failures=1) if failure == "cancel" else await journey.case(effect=True)
        )
        good_gate = journey.arm(uuid4().hex, "model.request", role="control", case_id=good["case_id"], request=1)
        bad_gate = journey.arm(
            uuid4().hex,
            "model.stream_paused" if failure == "cancel" else "model.request",
            role="control",
            case_id=bad["case_id"],
        )
        good_receipt, bad_receipt = await asyncio.gather(journey.start(good), journey.start(bad))
        await asyncio.gather(journey.reached(good_gate), journey.reached(bad_gate))
        # Both provider requests are in flight before either Run can settle.
        assert (await journey.live.run(good_receipt["run_id"]))["status"] == "running"
        assert (await journey.live.run(bad_receipt["run_id"]))["status"] == "running"
        if failure == "cancel":
            await journey.live.interrupt(bad_receipt["run_id"])
        else:
            journey.release(bad_gate)
        bad_run = await journey.live.finish(bad_receipt["run_id"], "cancelled" if failure == "cancel" else "failed")
        assert (await journey.live.run(good_receipt["run_id"]))["status"] == "running"
        journey.release(bad_gate)
        journey.release(good_gate)
        good_run = await journey.live.finish(good_receipt["run_id"])
        assert good["token"] in good_run["output_text"]
        assert len(journey.observations(good)) == 2 and len(journey.observations(bad)) == 1
        _, good_rows = await assert_usage(journey, good_run, requests=2, tokens=10)
        _, bad_rows = await assert_usage(journey, bad_run, requests=1, tokens=0 if failure == "cancel" else 10)
        assert {row["record"]["record_id"] for row in good_rows}.isdisjoint(
            row["record"]["record_id"] for row in bad_rows
        )
        assert bad["token"] not in json.dumps(journey.observations(good))
        assert good["token"] not in json.dumps(journey.observations(bad))
        if failure == "budget":
            assert bad_run["failure"]["code"] == "execution_usage_exhausted"
        await journey.live.assert_stable(lambda: journey.live.run(bad_run["id"]), bad_run, seconds=1)
