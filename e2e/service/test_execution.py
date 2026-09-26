"""Setup through the CLI and public API, idempotent submission, execution and continuation of a thread."""

import asyncio
from uuid import uuid4

import pytest
from a13n_service.runs.stream import stream_key

from .api import expect, message, transcript
from .scripted import user_texts
from .stack import run_cli
from .streams import Frame, assistant_text, finished, read_until, thread_stream

pytestmark = pytest.mark.anyio


async def test_setup_submission_and_continuation(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    config = stack.control.config
    # The template was migrated and bootstrapped through the same CLI: both commands are safe to repeat.
    again = run_cli(config, "migrate")
    assert again.returncode == 0, again.stderr
    second = run_cli(
        config, "bootstrap", "--email", "other@example.com", "--password-stdin", stdin="another-password\n"
    )
    assert second.returncode == 3 and "already initialized" in second.stderr
    session = expect(await api.client.get("/api/v1/auth/session"), 200)
    assert session["user"]["id"] == stack.stores.tenant["principal_id"]

    agent = await api.create_agent("helper", await api.create_model(model.base_url))
    await model.say("Hello from the scripted model.", to="[first]")
    body = message(agent, "[first] Say hello")
    key = uuid4().hex
    created = await api.client.post(f"{api.path}/threads", json=body, headers={"idempotency-key": key})
    receipt = expect(created, 201)
    thread_id, run_id = receipt["thread"]["id"], receipt["run"]["id"]
    replayed = expect(await api.client.post(f"{api.path}/threads", json=body, headers={"idempotency-key": key}), 200)
    assert (replayed["entry"]["id"], replayed["run"]["id"]) == (receipt["entry"]["id"], run_id)
    changed = await api.client.post(
        f"{api.path}/threads", json=message(agent, "[first] Say goodbye"), headers={"idempotency-key": key}
    )
    assert expect(changed, 409)["error"]["code"] == "conflict"

    first = await api.sealed(run_id)
    assert (first["status"], first["output"], first["attempts"]) == ("completed", "Hello from the scripted model.", 1)
    assert first["lineage"] == "root" and first["parent_run_id"] is None
    assert transcript(await api.items(run_id)) == [
        ("user", "[first] Say hello"),
        ("assistant", "Hello from the scripted model."),
    ]
    [attempt] = await api.attempts(run_id)
    assert (attempt["status"], attempt["start_reason"]) == ("succeeded", "initial")

    # A second message continues the completed head. Concurrent retries of one request create one entry.
    await model.say("Continued.", to="[second]")
    follow = message(agent, "[second] Continue")
    follow_key = uuid4().hex
    responses = await asyncio.gather(
        *(
            api.client.post(
                f"{api.path}/threads/{thread_id}/inbox", json=follow, headers={"idempotency-key": follow_key}
            )
            for _ in range(5)
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 200, 201]
    assert len({response.json()["entry"]["id"] for response in responses}) == 1
    continued = await api.sealed(
        next(response for response in responses if response.status_code == 201).json()["run"]["id"]
    )
    assert (continued["status"], continued["output"]) == ("completed", "Continued.")
    assert (continued["lineage"], continued["parent_run_id"]) == ("continue", run_id)
    assert [entry["status"] for entry in await api.inbox(thread_id)] == ["consumed", "consumed"]
    thread = await api.thread(thread_id)
    assert thread["head_run_id"] == thread["last_run_id"] == continued["id"] and thread["current_run_id"] is None

    [request] = await model.requests("[second]")
    assert user_texts(request) == ["[first] Say hello", "[second] Continue"]
    assert any(entry.get("content") == "Hello from the scripted model." for entry in request["body"]["messages"])


async def test_the_thread_stream_survives_redis_loss(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("helper", await api.create_model(model.base_url))
    first_reply = "Streamed output arrives in order, exactly once."
    await model.say(first_reply, to="[live]", chunks=6, delay=0.2)
    receipt = await api.start(agent, "[live] Stream a reply")
    thread_id, first_run = receipt["thread"]["id"], receipt["run"]["id"]
    async with thread_stream(api, thread_id) as frames:
        seen = await read_until(frames, finished(first_run))
    assert assistant_text(seen, first_run) == first_reply
    sequences = [frame.data["sequence"] for frame in seen if frame.event == "delta"]
    assert sequences == list(range(1, len(sequences) + 1))
    assert any(frame.event == "boundary" for frame in seen)
    assert (await api.sealed(first_run))["output"] == first_reply

    # Mid-reply, Redis loses the thread's stream. The client resumes from its last event, is told about the gap,
    # and converges on the durable view without resubmitting anything.
    second_reply = "After the loss, the durable view still holds every word once."
    await model.say(second_reply, to="[loss]", chunks=12, delay=0.25)
    second_run = (await api.send(thread_id, agent, "[loss] Stream again"))["run"]["id"]
    resumed_from = next(frame.id for frame in reversed(seen) if frame.id is not None)
    before: list[Frame] = []

    def replying(frame: Frame) -> bool:
        before.append(frame)
        return len(assistant_text(before, second_run)) >= len(second_reply) // 3

    async with thread_stream(api, thread_id, last_event_id=resumed_from) as frames:
        await read_until(frames, replying)
    await stack.redis.delete(stream_key(thread_id))
    last = next(frame.id for frame in reversed(before) if frame.id is not None)
    async with thread_stream(api, thread_id, last_event_id=last) as frames:
        after = await read_until(frames, finished(second_run))
    assert after[0].event == "gap" and after[0].data["run_id"] == second_run
    delivered = [
        (frame.data["run_id"], frame.data["attempt"], frame.data["sequence"])
        for frame in (*seen, *before, *after)
        if frame.event == "delta"
    ]
    assert len(delivered) == len(set(delivered)), "a delta was delivered twice"
    run = await api.sealed(second_run)
    items = await api.items(second_run)
    assert run["output"] == second_reply and items["complete"]
    assert transcript(items) == [("user", "[loss] Stream again"), ("assistant", second_reply)]
