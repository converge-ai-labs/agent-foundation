"""Contention: concurrent submissions to one thread and to many threads, executed by two competing workers."""

import asyncio
from itertools import pairwise

import pytest

from .api import eventually
from .scripted import user_texts
from .stack import read_rows

pytestmark = pytest.mark.anyio

BUSY_MESSAGES = 12
THREADS = 8
MESSAGES_PER_THREAD = 4


async def settled(api, thread_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
    """The thread's entries once it is idle with every entry consumed."""

    async def check() -> list[dict] | None:
        thread, entries = await api.thread(thread_id), await api.inbox(thread_id)
        idle = thread["current_run_id"] is None
        return entries if idle and all(entry["status"] == "consumed" for entry in entries) else None

    return await eventually(check, timeout=90)


async def test_concurrent_input_is_consumed_once_in_order(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("helper", await api.create_model(model.base_url))
    await model.say("Noted.", repeat=True)

    busy = (await api.start(agent, "[busy] 0"))["thread"]["id"]
    others = await asyncio.gather(*(api.start(agent, f"[t{index}] 0") for index in range(THREADS)))
    markers = {busy: "[busy]"} | {receipt["thread"]["id"]: f"[t{index}]" for index, receipt in enumerate(others)}
    await asyncio.gather(
        *(api.send(busy, agent, f"[busy] {number}") for number in range(1, BUSY_MESSAGES)),
        *(
            api.send(thread_id, agent, f"{marker} {number}")
            for thread_id, marker in markers.items()
            if thread_id != busy
            for number in range(1, MESSAGES_PER_THREAD)
        ),
    )

    for thread_id, marker in markers.items():
        entries = await settled(api, thread_id)
        runs = await api.runs(thread_id)
        assert all(run["status"] == "completed" for run in runs)
        # created_at is the transaction's start, which may precede its wait for the thread lock.
        # Check the continuation chain and execution intervals using the actual lifecycle clocks.
        runs.sort(key=lambda run: run["started_at"])
        assert all(
            later["parent_run_id"] == earlier["id"] and later["started_at"] >= earlier["sealed_at"]
            for earlier, later in pairwise(runs)
        )
        # FIFO consumption: entries are taken by runs in inbox order, each by exactly one run.
        order = {run["id"]: index for index, run in enumerate(runs)}
        taken = [order[entry["assigned_run_id"]] for entry in entries]
        assert taken == sorted(taken)
        assert {run["source_entry_id"] for run in runs} <= {entry["id"] for entry in entries}
        # The history the last run continued holds every input exactly once, in inbox order.
        final = (await model.requests(marker))[-1]
        submitted = [entry["payload"]["content"][0]["text"] for entry in entries]
        assert user_texts(final) == submitted and len(submitted) == len(set(submitted))
    workers = read_rows(
        stack.database,
        "SELECT DISTINCT worker_id FROM run_attempts WHERE workspace_id = :workspace_id",
        workspace_id=api.tenant["workspace_id"],
    )
    assert len(workers) == 2, "both workers should have claimed runs"
