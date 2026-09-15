"""Responses recovery and cancellation account only committed native usage."""

from uuid import uuid4

import pytest

from .test_usage_faults import evidence, journey_lab

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
async def responses_lab(request):
    async with journey_lab(request) as journey:
        await journey.patch(
            journey.base + "/models/" + journey.live.config["model_id"], {"model_api": "openai.responses"}
        )
        yield journey


@pytest.mark.parametrize("mode,failures", [("truncated", 1), ("truncated", 5), ("paused", 1)])
async def test_responses_partial_stream_never_invents_usage_or_success(responses_lab, mode, failures):
    journey = responses_lab
    case = await journey.case(failure=mode, failures=failures)
    barrier = journey.arm(uuid4().hex, "responses.stream_paused", role="control", case_id=case["case_id"])
    try:
        receipt = await journey.start(case)
        if mode == "paused":
            await journey.reached(barrier)
            async with journey.live.stream(receipt["run_id"]) as stream:
                async for event in stream:
                    if event.kind == "agui.text_message_content":
                        break
            await journey.live.interrupt(receipt["run_id"])
        expected = "cancelled" if mode == "paused" else "failed" if failures == 5 else "completed"
        run = await journey.live.finish(receipt["run_id"], expected)
        requests = 1 if mode == "paused" else 5 if failures == 5 else 2
        calls = journey.observations(case)
        assert len(calls) == requests and all(
            "input" in call["body"] and "messages" not in call["body"] for call in calls
        )
        assert "PARTIAL_" not in (run["output_text"] or "")
        execution, rows = await evidence(journey, run["id"])
        usage = execution["usage_charged"]
        tokens = (20, 5) if expected == "completed" else (0, 0)
        assert (usage["model_requests"], usage["input_tokens"], usage["output_tokens"]) == (requests, *tokens)
        assert len({row["record"]["record_id"] for row in rows}) == len(rows)
        assert all(row["run_attempt_id"] == execution["attempts"][0]["id"] for row in rows)
        for field, value in zip(("input_tokens", "output_tokens"), tokens, strict=True):
            assert sum(row["record"]["request_usage"][field] for row in rows) == value
        if expected == "completed":
            assert run["output_text"] == case["token"]
            completed = next(event for event in await journey.live.events(run["id"]) if event.kind == "run.completed")
            assert completed.data["payload"]["data"]["usage"] == usage
        elif expected == "failed":
            assert run["failure"]["code"] == "model_recovery_exhausted"
        journey.release(barrier)
        await journey.live.assert_stable(lambda: journey.live.run(run["id"]), run, seconds=1)
    finally:
        journey.close_barriers()
        await journey.live.cleanup()
