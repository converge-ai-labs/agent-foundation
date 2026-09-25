"""Worker process faults: a crash replaced after lease expiry, heartbeats through a slow model, and a drain."""

import asyncio
import signal

import pytest

from .api import eventually
from .scripted import tool_results, user_texts
from .stack import DRAIN_SECONDS, LEASE_SECONDS, read_rows

pytestmark = pytest.mark.anyio

# A tool without effects outside the Service, allowed by default once its toolset is enabled.
LOOKUP = {"toolsets": {"configuration": {"enabled": True}}}


def attempt_rows(stack, run_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
    """Which worker held each attempt and its lease clock; the public attempt view omits both."""
    return read_rows(
        stack.database,
        "SELECT number, worker_id, heartbeat_at, lease_expires_at, created_at FROM run_attempts"
        " WHERE run_id = :run_id ORDER BY number",
        run_id=run_id,
    )


async def tool_committed(api, run_id: str) -> None:  # type: ignore[no-untyped-def]
    """Wait until the run's committed items hold the completed tool call."""

    async def check() -> bool:
        items = (await api.items(run_id))["items"]
        return any(item["kind"] == "tool_call" and item["state"] == "completed" for item in items)

    await eventually(check)


async def test_a_killed_worker_is_replaced_after_its_lease_expires(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("helper", await api.create_model(model.base_url), **LOOKUP)
    await model.call("find_resources", {"kind": "model"}, call_id="call_find", to="[crash]")
    # The worker dies while this request is outstanding; its answer never comes.
    await model.say("Lost with the worker.", to="[crash]", hold="never")
    doomed = stack.workers[0]
    with stack.only(doomed):
        receipt = await api.start(agent, "[crash] Look the models up")
        run_id = receipt["run"]["id"]
        await model.arrived("[crash]", status="held")
    await tool_committed(api, run_id)

    doomed.signal(signal.SIGKILL)
    doomed.wait(5)
    await model.say("Recovered after the crash.", to="[crash]")
    run = await api.sealed(run_id, timeout=LEASE_SECONDS * 5)
    assert (run["status"], run["output"], run["attempts"]) == ("completed", "Recovered after the crash.", 2)
    attempts = await api.attempts(run_id)
    assert [(item["status"], item["start_reason"]) for item in attempts] == [
        ("failed", "initial"),
        ("succeeded", "recovery"),
    ]
    first, second = attempt_rows(stack, run_id)
    assert first["worker_id"] != second["worker_id"]
    assert second["created_at"] >= first["lease_expires_at"], "the replacement claimed a live lease"

    # The replacement continued from the committed checkpoint: the tool ran once and nothing was asked twice.
    requests = await model.requests("[crash]")
    assert [request["status"] for request in requests] == ["answered", "abandoned", "answered"]
    lost, retried = requests[1], requests[2]
    assert user_texts(retried) == user_texts(lost) == ["[crash] Look the models up"]
    assert len(tool_results(retried)) == 1 and tool_results(retried) == tool_results(lost)
    entries = await api.inbox(receipt["thread"]["id"])
    assert [(entry["status"], entry["assigned_run_id"]) for entry in entries] == [("consumed", run_id)]


async def test_heartbeats_keep_one_attempt_through_a_slow_model(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("helper", await api.create_model(model.base_url))
    await model.say("Worth the wait.", to="[slow]", hold="slow")
    run_id = (await api.start(agent, "[slow] Take your time"))["run"]["id"]
    await model.arrived("[slow]", status="held")
    [claimed] = attempt_rows(stack, run_id)

    # The request stays outstanding for three leases; renewals keep the attempt current, so no worker competes.
    await asyncio.sleep(LEASE_SECONDS * 3)
    [renewed] = attempt_rows(stack, run_id)
    assert (
        renewed["heartbeat_at"] > claimed["lease_expires_at"] and renewed["lease_expires_at"] > renewed["heartbeat_at"]
    )
    assert (await api.run(run_id))["status"] == "running"

    await model.open("slow")
    run = await api.sealed(run_id)
    assert (run["status"], run["output"], run["attempts"]) == ("completed", "Worth the wait.", 1)
    [attempt] = await api.attempts(run_id)
    assert (attempt["status"], attempt["start_reason"]) == ("succeeded", "initial")
    assert [request["status"] for request in await model.requests("[slow]")] == ["answered"]


async def test_a_draining_worker_hands_its_run_to_another(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("helper", await api.create_model(model.base_url), **LOOKUP)
    await model.call("find_resources", {"kind": "model"}, call_id="call_find", to="[drain]", hold="drain")
    draining, successor = stack.workers
    with stack.only(draining):
        receipt = await api.start(agent, "[drain] Look the models up")
        run_id = receipt["run"]["id"]
        await model.arrived("[drain]", status="held")

    async def checkpointed() -> bool:
        return [entry["status"] for entry in await api.inbox(receipt["thread"]["id"])] == ["consumed"]

    # The request starts before its boundary's checkpoint commits, and a drain before that commit yields there.
    await eventually(checkpointed)
    # Shutdown begins while the model request is outstanding; the attempt yields at its next model boundary.
    draining.signal(signal.SIGTERM)
    await eventually(lambda: asyncio.sleep(0, not draining.listening()))
    await model.open("drain")

    async def handed_off() -> bool:
        return [item["status"] for item in await api.attempts(run_id)][:1] == ["yielded"]

    await eventually(handed_off)
    # Uvicorn re-raises the signal it handled once its graceful shutdown completed.
    assert draining.wait(DRAIN_SECONDS) == -signal.SIGTERM
    await model.say("Finished on the other worker.", to="[drain]")
    run = await api.sealed(run_id)
    assert (run["status"], run["output"]) == ("completed", "Finished on the other worker.")
    attempts = await api.attempts(run_id)
    assert [(item["status"], item["start_reason"]) for item in attempts] == [
        ("yielded", "initial"),
        ("succeeded", "handoff"),
    ]
    first, second = attempt_rows(stack, run_id)
    assert first["worker_id"] != second["worker_id"] and successor.running

    # The tool ran once on the draining worker; the successor asked only for the next step.
    answered = [request for request in await model.requests("[drain]") if request["status"] == "answered"]
    assert len(answered) == 2 and len(tool_results(answered[1])) == 1
    assert user_texts(answered[1]) == ["[drain] Look the models up"]
