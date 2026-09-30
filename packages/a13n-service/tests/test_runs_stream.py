"""Typed producer batches, durable coverage and Redis replay recovery."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from a13n_service.runs import stream as stream_module
from a13n_service.runs.coalesce import Coalescer
from a13n_service.runs.display import Display
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.stream import ThreadStream, WrittenPosition, stream_key
from a13n_service.settings import Settings
from a13n_stream_protocol import DisplayDelta, DisplayProjector, DisplayScope, DisplayState, Producer
from a13n_stream_protocol.display import BlockPut, ScopePut
from pydantic_ai.messages import PartDeltaEvent, PartEndEvent, PartStartEvent, TextPart, TextPartDelta
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio
RUN = "run_stream"


def _trimming(settings: Settings, window: float) -> Settings:
    return settings.model_copy(update={"worker": settings.worker.model_copy(update={"stream_trim_seconds": window})})


async def _entries(redis: Redis, thread_id: str) -> list[tuple[str, dict[str, str]]]:
    return await redis.xrange(stream_key(thread_id))


def _batches(entries):
    return [DisplayDelta.model_validate_json(fields["delta"]) for _, fields in entries if "delta" in fields]


def _puts(entries):
    return [op.block for delta in _batches(entries) for op in delta.operations if isinstance(op, BlockPut)]


def _delta(sequence: int, run_id: str = RUN, *, attempt: int = 1) -> DisplayDelta:
    return DisplayDelta(
        producer=Producer(run_id=run_id, generation=str(attempt)),
        from_sequence=sequence - 1,
        through_sequence=sequence,
        operations=(ScopePut(scope=DisplayScope(id="scope", thread_id="thread", run_id="harness")),),
    )


async def _produce(runtime: Runtime, *, window: float, pieces: list[str], cancel: bool = False):
    thread_id = f"thr_{uuid4().hex}"
    baseline = Display.empty(RUN, attempt=1).snapshot
    live = ThreadStream(runtime.redis, runtime.settings, thread_id=thread_id, run_id=RUN, attempt=1)
    projector = DisplayProjector(baseline, publish=live.delta, batch=window > 0)
    try:
        async with live, Coalescer(projector, window=window):
            projector.scope(DisplayScope(id="scope", thread_id="thread", run_id="harness"))
            projector.observe("scope", 0, PartStartEvent(index=0, part=TextPart("")))
            for index, piece in enumerate(pieces):
                projector.observe("scope", 0, PartDeltaEvent(index=0, delta=TextPartDelta(piece)))
                if index % 100 == 0:
                    await live.buffer.join()
            if cancel:
                raise asyncio.CancelledError
            projector.observe("scope", 0, PartEndEvent(index=0, part=TextPart("".join(pieces))))
    except asyncio.CancelledError:
        pass
    entries = await _entries(runtime.redis, thread_id)
    replay = DisplayState(baseline)
    for delta in _batches(entries):
        replay.apply(delta)
    assert replay.capture() == projector.capture()
    return entries, projector.capture()


async def test_batches_are_dense_and_replay_the_same_display(runtime: Runtime) -> None:
    each, immediate = await _produce(runtime, window=0, pieces=["tok "] * 400)
    batches, batched = await _produce(runtime, window=60, pieces=["tok "] * 400)
    assert batched.blocks == immediate.blocks
    assert len(batches) < len(each) // 10
    assert [delta.through_sequence for delta in _batches(batches)] == list(range(1, len(batches) + 1))


async def test_batch_size_and_attempt_end_bound_pending_operations(runtime: Runtime) -> None:
    entries, final = await _produce(runtime, window=60, pieces=["x" * 5000] * 10, cancel=True)
    assert final.blocks[0].content["text"] == "x" * 50000
    assert len(entries) > 1
    assert all(len(delta.operations) <= 128 for delta in _batches(entries))


async def test_timer_publishes_without_public_stream_consumption(runtime: Runtime) -> None:
    delivered = asyncio.Event()
    projector = DisplayProjector(
        Display.empty(RUN, attempt=1).snapshot, publish=lambda delta: delivered.set(), batch=True
    )
    async with Coalescer(projector, window=0.01):
        projector.scope(DisplayScope(id="scope", thread_id="thread", run_id="harness"))
        async with asyncio.timeout(2):
            await delivered.wait()
        assert projector.state.position.sequence == 1


async def test_a_streamed_reply_reaches_the_stream_coalesced(executing, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(executing, scripted_model)
    pieces = ["tok "] * 400
    scripted_model.say(*pieces)
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "fast"))["run"]["id"])
    entries = await _entries(executing.runtime.redis, run["thread_id"])
    listing = await runs_kit.items(executing, run["id"])
    assert runs_kit.texts(listing) == [("user", "fast"), ("assistant", "".join(pieces))]
    replay = DisplayState(Display.empty(run["id"], attempt=1).snapshot)
    batches = _batches(entries)
    for delta in batches:
        replay.apply(delta)
    assert replay.capture().blocks == tuple(Display.model_validate({"snapshot": listing["snapshot"]}).snapshot.blocks)
    assert len(batches) < len(pieces) // 10
    last_id, fields = [(entry_id, fields) for entry_id, fields in entries if "delta" in fields][-1]
    assert listing["resume_after"] == last_id
    assert listing["position"] == f"1-{fields['sequence']}"


@pytest.mark.parametrize("failure", ["dropped", "pending"])
@pytest.mark.parametrize("previous", [False, True])
async def test_writer_keeps_only_confirmed_positions(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, failure: str, previous: bool
) -> None:
    settings = runtime.settings.model_copy(update={"redis": runtime.settings.redis.model_copy(update={"timeout": 0.1})})
    live = ThreadStream(runtime.redis, settings, thread_id=f"thr_{uuid4().hex}", run_id=RUN, attempt=2)
    append = stream_module.append
    release = asyncio.Event()
    started = asyncio.Event()

    async def interrupted(*args: Any, **kwargs: Any) -> list[str]:
        started.set()
        if failure == "dropped":
            return []
        await release.wait()
        return await append(*args, **kwargs)

    async with live:
        expected = None
        if previous:
            live.delta(_delta(1))
            await live.buffer.join()
            expected = live.last_written
            entries = await _entries(runtime.redis, live.key.removeprefix(stream_module.STREAM_PREFIX))
            assert expected == WrittenPosition(2, 1, entries[0][0])
        monkeypatch.setattr(stream_module, "append", interrupted)
        live.delta(_delta(2))
        try:
            await started.wait()
            assert live.last_written == expected
            assert live.writer is not None and not live.writer.cancelling()
        finally:
            release.set()
        await live.buffer.join()
        monkeypatch.setattr(stream_module, "append", append)
        live.delta(_delta(3))
        await live.buffer.join()
        recovered = live.last_written
        assert recovered is not None and recovered.sequence == 3
        live.boundary(3)
        await live.buffer.join()
        assert live.last_written == recovered  # The later boundary never replaces the delta's cursor.
    assert live.last_written == recovered  # Terminal persistence reads the position after close.


@pytest.mark.parametrize("previous", [False, True])
async def test_stream_close_timeout_keeps_the_confirmed_position(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, previous: bool
) -> None:
    settings = runtime.settings.model_copy(update={"redis": runtime.settings.redis.model_copy(update={"timeout": 0.1})})
    live = ThreadStream(runtime.redis, settings, thread_id=f"thr_{uuid4().hex}", run_id=RUN, attempt=2)
    started = asyncio.Event()

    async def blocked(*args: Any, **kwargs: Any) -> list[str]:
        started.set()
        await asyncio.Event().wait()
        return []

    async with live:
        if previous:
            live.delta(_delta(1))
            await live.buffer.join()
        confirmed = live.last_written
        assert (confirmed is not None) == previous
        monkeypatch.setattr(stream_module, "append", blocked)
        live.delta(_delta(2))
        await started.wait()
    assert live.last_written == confirmed
    assert live.writer is not None and live.writer.cancelling()


async def test_checkpoint_does_not_wait_for_pending_redis_writes(
    serve, settings, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    # A checkpoint must finish while the writer is blocked, long before its configured timeout.
    slow = settings.model_copy(update={"redis": settings.redis.model_copy(update={"timeout": 60})})
    append = stream_module.append
    started, release, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked(*args: Any, **kwargs: Any) -> list[str]:
        started.set()
        await release.wait()
        return await append(*args, **kwargs)

    monkeypatch.setattr(stream_module, "append", blocked)
    async with serve(settings=slow) as service:
        agent = await runs_kit.create_agent(service, scripted_model)
        scripted_model.say("Done", gate=gate)
        submitted = await runs_kit.start_thread(service, agent, "hi")
        run_id = submitted["run"]["id"]
        task = await runs_kit.attempt(service)
        try:
            async with asyncio.timeout(10):
                await started.wait()
                await scripted_model.request()
                await runs_kit.checkpointed(service, run_id)
                listing = await runs_kit.items(service, run_id)
            assert not release.is_set()
            assert listing["position"] is not None and not listing["complete"]
            assert listing["resume_after"] is None
            assert runs_kit.texts(listing) == [("user", "hi")]
        finally:
            release.set()
            gate.set()
            await task
        assert (await runs_kit.items(service, run_id))["resume_after"] is not None


async def test_final_snapshot_keeps_an_earlier_cursor_when_redis_writes_fail(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    append = stream_module.append
    confirmed = asyncio.Event()

    async def tracked(*args: Any, **kwargs: Any) -> list[str]:
        ids = await append(*args, **kwargs)
        if ids:
            confirmed.set()
        return ids

    monkeypatch.setattr(stream_module, "append", tracked)
    agent = await runs_kit.create_agent(service, scripted_model)
    gate = asyncio.Event()
    scripted_model.say("Still saved", gate=gate)
    submitted = await runs_kit.start_thread(service, agent, "hi")
    run_id, thread_id = submitted["run"]["id"], submitted["thread"]["id"]
    task = await runs_kit.attempt(service)
    try:
        await scripted_model.request()
        await runs_kit.checkpointed(service, run_id)
        async with asyncio.timeout(10):
            await confirmed.wait()

        async def dropped(*args: Any, **kwargs: Any) -> list[str]:
            return []

        monkeypatch.setattr(stream_module, "append", dropped)
    finally:
        gate.set()
        await task
    listing = await runs_kit.items(service, run_id)
    assert listing["complete"] and listing["run"]["status"] == "completed"
    assert runs_kit.texts(listing) == [("user", "hi"), ("assistant", "Still saved")]
    entries = await _entries(service.runtime.redis, thread_id)
    last_id, fields = [(entry_id, fields) for entry_id, fields in entries if "boundary" not in fields][-1]
    assert listing["resume_after"] == last_id
    assert int(fields["sequence"]) < int(listing["position"].split("-")[1])


async def test_a_boundary_trims_what_its_display_covers(runtime: Runtime) -> None:
    thread_id = f"thr_{uuid4().hex}"
    async with ThreadStream(
        runtime.redis, _trimming(runtime.settings, 0), thread_id=thread_id, run_id=RUN, attempt=1
    ) as live:
        live.delta(_delta(1))
        live.delta(_delta(2))
        live.boundary(2)
        live.delta(_delta(3))
    assert [(fields["sequence"], "boundary" in fields) for _, fields in await _entries(runtime.redis, thread_id)] == [
        ("2", True),
        ("3", False),
    ]

    # With a retention window, covered entries younger than the window stay for readers that resume.
    thread_id = f"thr_{uuid4().hex}"
    async with ThreadStream(
        runtime.redis, _trimming(runtime.settings, 0.3), thread_id=thread_id, run_id=RUN, attempt=1
    ) as live:
        live.delta(_delta(1))
        await asyncio.sleep(0.6)
        live.delta(_delta(2))
        live.boundary(2)
        live.delta(_delta(3))
    assert [(fields["sequence"], "boundary" in fields) for _, fields in await _entries(runtime.redis, thread_id)] == [
        ("2", False),
        ("2", True),
        ("3", False),
    ]


async def test_a_run_keeps_only_the_tail_after_its_latest_boundary(serve, settings, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    worker = settings.worker.model_copy(update={"scan_seconds": 0.05, "authority_seconds": 0.05})
    fast = _trimming(settings.model_copy(update={"worker": worker}), 0)
    async with serve(settings=fast, role="all") as service:
        agent = await runs_kit.create_agent(service, scripted_model)
        scripted_model.say("Kept")
        run = await runs_kit.sealed(service, (await runs_kit.start_thread(service, agent, "Removed"))["run"]["id"])
        entries = await _entries(service.runtime.redis, run["thread_id"])

    # The input streamed before the checkpoint that consumed it; the reply after it.
    assert "boundary" in entries[0][1] and "Removed" not in json.dumps(entries)
    assert any(block.content.get("text") == "Kept" for block in _puts(entries))


async def _queued_run(service, scripted_model, runs_kit) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A thread whose run waits for a worker the control role does not run: the stream's current run."""
    agent = await runs_kit.create_agent(service, scripted_model)
    return (await runs_kit.start_thread(service, agent, "hi"))["run"]


async def _frames(stream: Any, count: int) -> list[tuple[str, Any]]:
    async with asyncio.timeout(10):
        return [(event, data.get("sequence")) for event, _, data in [await anext(stream) for _ in range(count)]]


async def test_readers_after_a_trim_receive_the_tail_and_gaps(service, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.claim import claim

    run = await _queued_run(service, scripted_model, runs_kit)
    await claim(service.runtime, worker_id="trim-test", worker_build="test", limit=1)
    settings = _trimming(service.runtime.settings, 0)
    async with ThreadStream(
        service.runtime.redis, settings, thread_id=run["thread_id"], run_id=run["id"], attempt=1
    ) as live:
        live.delta(_delta(1))
        live.delta(_delta(2))
        live.boundary(2)
        live.delta(_delta(3))
    boundary_id = (await _entries(service.runtime.redis, run["thread_id"]))[0][0]
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/threads/{run['thread_id']}/stream"
        # A new reader receives only the tail; the boundary past what it received tells it to re-read the display.
        async with runs_kit.frames(url, headers) as stream:
            assert await _frames(stream, 3) == [("gap", None), ("boundary", 2), ("delta", 3)]
        # A reader resuming exactly at the boundary continues without a gap.
        async with runs_kit.frames(
            url + f"?run={run['id']}&position=1-2", {**headers, "last-event-id": boundary_id}
        ) as stream:
            assert await _frames(stream, 1) == [("delta", 3)]
        # A reader whose position was removed starts with a gap.
        async with runs_kit.frames(
            url + f"?run={run['id']}&position=1-0", {**headers, "last-event-id": "1-0"}
        ) as stream:
            assert (await _frames(stream, 1))[0] == ("gap", None)


async def test_a_live_reader_skipped_past_removed_entries_gets_a_gap(service, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]
    run = await _queued_run(service, scripted_model, runs_kit)
    redis, key = service.runtime.redis, stream_key(run["thread_id"])
    fields = {"run_id": run["id"], "attempt": "1"}

    def delta(sequence: int) -> dict[str, str]:
        return {**fields, "sequence": str(sequence), "delta": _delta(sequence, run["id"]).model_dump_json()}

    first = await redis.xadd(key, delta(1))
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/threads/{run['thread_id']}/stream"
        async with runs_kit.frames(url, headers) as stream:
            assert await _frames(stream, 1) == [("delta", 1)]
            # Trimmed before the shared read reached them, as a lagging reader experiences it.
            milliseconds = int(first.partition("-")[0]) + 1
            async with redis.pipeline(transaction=True) as pipe:
                pipe.xadd(key, delta(2), id=f"{milliseconds}-0")
                pipe.xadd(key, {**fields, "sequence": "2", "boundary": "1"}, id=f"{milliseconds}-1")
                pipe.xadd(key, delta(3), id=f"{milliseconds}-2")
                pipe.xtrim(key, minid=f"{milliseconds}-1", approximate=False)
                await pipe.execute()
            assert await _frames(stream, 3) == [("gap", None), ("boundary", 2), ("delta", 3)]


async def test_cancelling_stream_close_still_signals_its_writer(runtime: Runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    stream = ThreadStream(runtime.redis, runtime.settings, thread_id="thread_close", run_id=RUN, attempt=1)
    joining = asyncio.Event()

    async def blocked_writer() -> None:
        await asyncio.Event().wait()

    async def blocked_join() -> None:
        joining.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(stream, "_write", blocked_writer)
    monkeypatch.setattr(stream.buffer, "join", blocked_join)
    await stream.__aenter__()
    closing = asyncio.create_task(stream.__aexit__())
    try:
        await joining.wait()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert stream.writer is not None and stream.writer.cancelling()
    finally:
        assert stream.writer is not None
        stream.writer.cancel()
        await asyncio.gather(stream.writer, return_exceptions=True)


@pytest.mark.parametrize("hint", ["valid", "absent", "expired", "ahead", "other_run", "old_attempt"])
async def test_snapshot_position_filters_replay_and_ignores_unsafe_hints(
    service, scripted_model, listen, runs_kit, hint
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.claim import claim

    run = await _queued_run(service, scripted_model, runs_kit)
    await claim(service.runtime, worker_id="resume-test", worker_build="test", limit=1)
    redis, key = service.runtime.redis, stream_key(run["thread_id"])

    async def put(sequence: int, **extra: str) -> str:
        return await redis.xadd(
            key,
            {
                "run_id": run["id"],
                "attempt": "1",
                "sequence": str(sequence),
                "delta": _delta(sequence, run["id"]).model_dump_json(),
                **extra,
            },
        )

    cursor = await put(80)
    await put(90)
    await put(100)
    await put(100, boundary="1")
    ahead = await put(101)
    if hint == "expired":
        await redis.xtrim(key, minid=ahead, approximate=False)
    elif hint == "ahead":
        cursor = ahead
    elif hint == "other_run":
        cursor = await put(100, run_id="run_other")
    elif hint == "old_attempt":
        cursor = await put(100, attempt="0")
    headers = await runs_kit.bearer(service)
    if hint != "absent":
        headers["Last-Event-ID"] = cursor
    async with listen(service.app) as base:
        url = f"{base}{service.api}/threads/{run['thread_id']}/stream?run={run['id']}&position=1-100"
        async with runs_kit.frames(url, headers) as frames:
            expected = [("delta", 101)] if hint == "expired" else [("boundary", 100), ("delta", 101)]
            assert await _frames(frames, len(expected)) == expected


async def test_snapshot_position_reports_only_the_missing_suffix(service, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.claim import claim

    run = await _queued_run(service, scripted_model, runs_kit)
    await claim(service.runtime, worker_id="resume-test", worker_build="test", limit=1)
    await service.runtime.redis.xadd(
        stream_key(run["thread_id"]),
        {
            "run_id": run["id"],
            "attempt": "1",
            "sequence": "151",
            "delta": _delta(151, run["id"]).model_dump_json(),
        },
    )
    async with listen(service.app) as base:
        url = f"{base}{service.api}/threads/{run['thread_id']}/stream?run={run['id']}&position=1-100"
        async with runs_kit.frames(url, await runs_kit.bearer(service)) as frames:
            async with asyncio.timeout(10):
                event, _, data = await anext(frames)
            assert event == "gap" and data == {"run_id": run["id"], "position": "1-150"}
            assert await _frames(frames, 1) == [("delta", 151)]


@pytest.mark.parametrize("query", ["run_only", "position_only", "malformed", "future", "wrong_thread"])
async def test_resume_claim_is_validated_before_streaming(service, scripted_model, runs_kit, query) -> None:  # type: ignore[no-untyped-def]
    run = await _queued_run(service, scripted_model, runs_kit)
    params = {"run": run["id"], "position": "0-0"}
    if query == "run_only":
        params.pop("position")
    elif query == "position_only":
        params.pop("run")
    elif query == "malformed":
        params["position"] = "01-2"
    elif query == "future":
        params["position"] = "2-0"
    else:
        other = await runs_kit.start_thread(service, {"id": run["agent_id"]}, "other")
        params["run"] = other["run"]["id"]
    response = await service.client.get(f"{service.api}/threads/{run['thread_id']}/stream", params=params)
    assert response.status_code == 400, response.text


async def test_final_display_does_not_need_redis(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("Saved")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "hi"))["run"]["id"])
    before = await runs_kit.items(executing, run["id"])
    await executing.runtime.redis.delete(stream_key(run["thread_id"]))
    assert await runs_kit.items(executing, run["id"]) == before


async def test_resuming_an_older_attempt_resets_before_the_new_attempt_tail(
    service, scripted_model, listen, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.claim import claim
    from a13n_service.runs.seal import release_attempt

    run = await _queued_run(service, scripted_model, runs_kit)
    first = (await claim(service.runtime, worker_id="resume-test", worker_build="test", limit=1))[0]
    await release_attempt(service.runtime, first, status="yielded", yield_reason="handoff")
    second = (await claim(service.runtime, worker_id="resume-test-2", worker_build="test", limit=1))[0]
    assert second.number == 2
    redis, key = service.runtime.redis, stream_key(run["thread_id"])
    await redis.xadd(
        key, {"run_id": run["id"], "attempt": "1", "sequence": "100", "delta": _delta(100, run["id"]).model_dump_json()}
    )
    await redis.xadd(
        key,
        {
            "run_id": run["id"],
            "attempt": "2",
            "sequence": "1",
            "delta": _delta(1, run["id"], attempt=2).model_dump_json(),
        },
    )
    # A fenced-out worker can finish an old Redis write after the new attempt starts.
    cursor = await redis.xadd(
        key, {"run_id": run["id"], "attempt": "1", "sequence": "100", "delta": _delta(100, run["id"]).model_dump_json()}
    )
    headers = {**await runs_kit.bearer(service), "Last-Event-ID": cursor}
    async with listen(service.app) as base:
        url = f"{base}{service.api}/threads/{run['thread_id']}/stream?run={run['id']}&position=1-100"
        async with runs_kit.frames(url, headers) as frames:
            assert await _frames(frames, 2) == [("reset", None), ("delta", 1)]


async def test_failed_checkpoint_publication_never_enqueues_a_boundary_or_trims(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs import checkpoints

    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.say("Unused")
    submitted = await runs_kit.start_thread(service, agent, "hi")
    trimmed = []

    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise ServiceError("payload_too_large", "Checkpoint publication failed")

    async def trim(*args: Any, **kwargs: Any) -> None:
        trimmed.append(kwargs)

    monkeypatch.setattr(checkpoints, "publish_checkpoint", fail)
    monkeypatch.setattr(stream_module, "trim", trim)
    await (await runs_kit.attempt(service))
    listing = await runs_kit.items(service, submitted["run"]["id"])
    assert listing["run"]["status"] == "failed"
    entries = await _entries(service.runtime.redis, submitted["thread"]["id"])
    assert all("boundary" not in fields for _, fields in entries)
    assert not trimmed


async def test_delayed_boundary_neither_trims_nor_seeks_past_uncommitted_output(
    service, scripted_model, listen, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.claim import claim

    run = await _queued_run(service, scripted_model, runs_kit)
    await claim(service.runtime, worker_id="delayed-boundary", worker_build="test", limit=1)
    async with ThreadStream(
        service.runtime.redis,
        _trimming(service.runtime.settings, 0),
        thread_id=run["thread_id"],
        run_id=run["id"],
        attempt=1,
    ) as live:
        live.delta(_delta(1))
        # Produced while persistence of snapshot 1 awaits object storage / SQL.
        live.delta(_delta(2))
        live.boundary(1)
    entries = await _entries(service.runtime.redis, run["thread_id"])
    assert [(fields["sequence"], "boundary" in fields) for _, fields in entries] == [
        ("1", False),
        ("2", False),
        ("1", True),
    ]
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/threads/{run['thread_id']}/stream?run={run['id']}&position=1-1"
        async with runs_kit.frames(url, {**headers, "last-event-id": entries[-1][0]}) as stream:
            assert await _frames(stream, 2) == [("delta", 2), ("boundary", 1)]


async def test_sql_refresh_announces_quiet_tail_without_redis_delta_or_notice(
    service, scripted_model, listen, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.infra.db import transaction
    from a13n_service.runs.claim import claim
    from a13n_service.runs.tables import RunRow

    run = await _queued_run(service, scripted_model, runs_kit)
    await claim(service.runtime, worker_id="quiet-tail", worker_build="test", limit=1)
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/threads/{run['thread_id']}/stream?run={run['id']}&position=1-0"
        async with runs_kit.frames(url, headers) as stream:
            async with transaction(service.runtime.storage) as session:
                row = await session.get(RunRow, run["id"])
                assert row is not None
                # The stream reads only selected pointer identity and coverage.
                row.display = {
                    "key": "quiet-tail",
                    "digest": "a" * 64,
                    "size": 1,
                    "format": 2,
                    "position": {"attempt": 1, "sequence": 2},
                }
            frames = await runs_kit.until(stream, lambda frame: frame[0] == "boundary")
            assert any(event == "gap" and data["position"] == "1-2" for event, _, data in frames)
            assert frames[-1][1] is None and frames[-1][2]["sequence"] == 2
        # Joining after the quiet tail also observes SQL coverage, without a version change.
        async with runs_kit.frames(url, headers) as stream:
            assert await _frames(stream, 2) == [("gap", None), ("boundary", 2)]
