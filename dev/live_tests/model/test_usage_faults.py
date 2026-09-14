"""Real Worker, native HTTP stream and durable receipt accounting at failure boundaries."""

import signal
from contextlib import asynccontextmanager
from uuid import uuid4

import anyio
import pytest

from ..infrastructure.round_two_lab import open_lab
from ..run_recovery.run_fault_support import RunFaultJourney

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def journey_lab(request, *, limit=None):
    if not request.config.getoption("--live-management"):
        pytest.skip("Opt in with --live-management; no infrastructure or credential reads by default")
    options = {"usage": True, "queue_faults": False, "worker": {"lease_seconds": 12}}
    if limit is not None:
        options["policy"] = {"max_usage": limit}
    async with open_lab(suite="management", local_connectors=False, run_faults=options) as lab:
        journey = RunFaultJourney(lab)
        await journey.setup()
        # Use the separate TLS peer so a broken chunked stream reaches the SDK
        # directly, as in the existing dependency-fault journeys.
        await journey.patch(
            journey.base + "/model-providers/" + journey.live.config["model_provider_id"],
            {
                "configuration": {
                    "base_url": journey.live.config["peer_url"] + "/__live__/model/v1",
                    "auth_mode": "bearer",
                }
            },
        )
        try:
            yield journey
        finally:
            journey.close_barriers()


@pytest.fixture(scope="module")
async def usage_lab(request):
    async with journey_lab(request) as journey:
        yield journey


@pytest.fixture
async def usage_journey(usage_lab):
    try:
        yield usage_lab
    finally:
        usage_lab.close_barriers()
        await usage_lab.live.cleanup()


async def evidence(journey, run_id):
    execution = await journey.execution(run_id)
    rows = (await journey.live.request("GET", f"/__live__/faults/runs/{run_id}/usage"))["items"]
    return execution, rows


async def assert_usage(journey, run, *, requests, tokens, receipt_tokens=None):
    execution, rows = await evidence(journey, run["id"])
    usage = execution["usage_charged"]
    assert (usage["model_requests"], usage["input_tokens"], usage["output_tokens"]) == (requests, tokens, tokens), (
        usage,
        rows,
    )
    assert len({row["record"]["record_id"] for row in rows}) == len(rows)
    assert all(row["run_attempt_id"] in {attempt["id"] for attempt in execution["attempts"]} for row in rows)
    for field in ("input_tokens", "output_tokens"):
        assert sum(row["record"]["request_usage"][field] for row in rows) == (
            tokens if receipt_tokens is None else receipt_tokens
        ), rows
    if run["status"] == "completed":
        completed = next(event for event in await journey.live.events(run["id"]) if event.kind == "run.completed")
        assert completed.data["payload"]["data"]["usage"] == usage
    return execution, rows


@pytest.mark.parametrize(
    "failure,failures,tokens",
    [("truncated", 1, 10), ("usage_truncated", 1, 20), ("usage_timeout", 1, 20), ("usage_truncated", 5, 50)],
)
async def test_stream_failure_accounts_only_observed_usage(usage_journey, failure, failures, tokens):
    journey = usage_journey
    agent = await journey.agent(model={"model_key": "live-fixture", "settings": {"timeout": 2}})
    case = await journey.case(failure=failure, failures=failures, delay_seconds=8)
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    run = await journey.live.finish(receipt["run_id"], "failed" if failures == 5 else "completed")
    assert "PARTIAL_" not in (run["output_text"] or "")
    requests = 5 if failures == 5 else 2
    assert len(journey.observations(case)) == requests
    await assert_usage(journey, run, requests=requests, tokens=tokens)


@pytest.mark.parametrize("committed", [False, True])
async def test_interrupt_preserves_committed_usage_without_inventing_missing_tokens(usage_journey, committed):
    journey = usage_journey
    case = await journey.case(effect=True) if committed else await journey.case(failure="paused", failures=1)
    barrier = journey.arm(
        uuid4().hex,
        "tool.before_effect" if committed else "model.stream_paused",
        role="worker" if committed else "control",
        case_id=case["case_id"],
    )
    receipt = await journey.start(case)
    await journey.reached(barrier)
    if committed:
        before, rows = await evidence(journey, receipt["run_id"])
        assert len(rows) == 1 and before["attempts"][0]["usage"]["input_tokens"] == 10
    else:
        # Observe streamed content before cancellation, without mistaking bytes
        # written upstream for a native usage receipt consumed by the SDK.
        async with journey.live.stream(receipt["run_id"]) as stream:
            async for event in stream:
                if event.kind == "agui.text_message_content":
                    break
    await journey.live.interrupt(receipt["run_id"])
    run = await journey.live.finish(receipt["run_id"], "cancelled")
    journey.release(barrier)
    await assert_usage(journey, run, requests=1, tokens=10 if committed else 0)
    assert len(journey.observations(case)) == 1
    assert journey.effects(case) == []
    await journey.live.assert_stable(lambda: journey.live.run(run["id"]), run, seconds=1)


async def test_late_receipt_preserves_attribution_without_rewriting_cancelled_run(usage_journey):
    journey = usage_journey
    barrier = journey.arm(uuid4().hex, "usage.delivery_delayed", records=1)
    ingested = journey.arm(uuid4().hex, "usage.after_ingest", action="observe", records=1)
    receipt = await journey.start(await journey.case())
    await journey.reached(barrier)
    assert (await evidence(journey, receipt["run_id"]))[1] == []
    await journey.live.interrupt(receipt["run_id"])
    run = await journey.live.finish(receipt["run_id"], "cancelled")
    journey.release(barrier)
    with anyio.fail_after(10):
        await journey.reached(ingested)
    await assert_usage(journey, run, requests=1, tokens=0, receipt_tokens=10)
    await journey.live.assert_stable(lambda: journey.live.run(run["id"]), run, seconds=1)


@pytest.mark.parametrize("point", ["usage.after_ingest", "checkpoint.after"])
async def test_worker_replacement_preserves_committed_receipts(usage_journey, point):
    journey, lab = usage_journey, usage_journey.lab
    match = {"kind": "completed"} if point == "checkpoint.after" else {"fence": 1}
    barrier = journey.arm(uuid4().hex, point, **match)
    case = await journey.case()
    receipt = await journey.start(case)
    await journey.reached(barrier)
    before, retained = await evidence(journey, receipt["run_id"])
    assert len(retained) == 1 and before["attempts"][0]["usage"]["input_tokens"] == 10
    owner = next(worker for worker in reversed(lab.workers) if worker.returncode is None)
    await lab.stop(owner, signal.SIGKILL)
    journey.release(barrier)
    await lab.start_worker()
    run, attempts = await journey.assert_settled(receipt)
    assert len(attempts) == 2
    requests = 1 if point == "checkpoint.after" else 2
    assert len(journey.observations(case)) == requests
    _, rows = await assert_usage(journey, run, requests=requests, tokens=requests * 10)
    assert all(row in rows for row in retained)
    assert sum(row["run_attempt_id"] == attempts[0]["id"] for row in rows) == 1


async def test_redelivery_and_terminal_snapshot_do_not_double_charge(usage_journey):
    journey = usage_journey
    replay = journey.arm(uuid4().hex, "usage.redeliver", action="observe")
    applied = journey.arm(uuid4().hex, "usage.replayed", action="observe")
    case = await journey.case()
    run = await journey.live.finish((await journey.start(case))["run_id"])
    await journey.reached(replay)
    await journey.reached(applied)
    _, rows = await assert_usage(journey, run, requests=1, tokens=10)
    assert len(rows) == 1 and len(journey.observations(case)) == 1
    async with journey.outsider() as outsider:
        assert (await outsider.get(f"/__live__/faults/runs/{run['id']}/usage")).status_code == 403


@pytest.fixture(scope="module", params=["input_tokens", "output_tokens"])
async def budget_lab(request):
    async with journey_lab(request, limit={request.param: 10}) as journey:
        yield journey


@pytest.mark.parametrize("mode", ["below", "exact_finish", "exact_next", "over_next"])
async def test_token_ceiling_preserves_incurred_usage_and_gates_next_request(budget_lab, mode):
    journey = budget_lab
    amount = 5 if mode == "below" else 11 if mode == "over_next" else 10
    case = await journey.case(
        effect=mode != "exact_finish",
        usage={"prompt_tokens": amount, "completion_tokens": amount, "total_tokens": 2 * amount},
    )
    receipt = await journey.start(case)
    try:
        run = await journey.live.wait(
            lambda: journey.live.run(receipt["run_id"]), lambda value: value["sealed_at"], receipt["run_id"]
        )
        requests = 2 if mode == "below" else 1
        observed = len(journey.observations(case))
        assert observed == requests, {
            "error": "An exhausted token ceiling allowed another provider call",
            "observed_requests": observed,
            "run_status": run["status"],
            "evidence": await evidence(journey, run["id"]),
        }
        assert run["status"] == ("completed" if mode in {"below", "exact_finish"} else "failed")
        if run["status"] == "failed":
            assert run["failure"]["code"] == "execution_usage_exhausted"
        await assert_usage(journey, run, requests=requests, tokens=requests * amount)
    finally:
        journey.close_barriers()
        await journey.live.cleanup()


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
async def test_zero_token_budget_prevents_provider_io(request, field):
    async with journey_lab(request, limit={field: 0}) as journey:
        case = await journey.case()
        receipt = await journey.start(case)
        run = await journey.live.wait(
            lambda: journey.live.run(receipt["run_id"]), lambda value: value["sealed_at"], receipt["run_id"]
        )
        assert journey.observations(case) == [], {
            "error": "A zero token budget still invoked the provider",
            "observed_requests": len(journey.observations(case)),
            "run_status": run["status"],
            "evidence": await evidence(journey, run["id"]),
        }
        assert run["status"] == "failed"
        assert run["failure"]["code"] == "execution_usage_exhausted"
        _, rows = await assert_usage(journey, run, requests=0, tokens=0)
        assert rows == []
