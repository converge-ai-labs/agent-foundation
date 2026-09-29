"""The thread stream: coalesced fragments, trimming at boundaries, and what readers receive after a trim."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from a13n_harness import HarnessEvent
from a13n_service.runs import stream as stream_module
from a13n_service.runs.coalesce import MAX_MERGED_CHARS, Coalescer
from a13n_service.runs.display import Display, DisplayFold, Observed
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.stream import ThreadStream, WrittenPosition, stream_key
from a13n_service.settings import Settings
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio

RUN = "run_stream"
PART_DELTA = "a13n.pydantic_ai.part_delta"
_AT = datetime(2026, 9, 24, tzinfo=UTC)


def _harness(native: list[Any]) -> list[HarnessEvent]:
    return [
        HarnessEvent(
            thread_id="thread",
            run_id="harness",
            sequence=index,
            occurred_at=_AT + timedelta(milliseconds=index),
            event=event,
        )
        for index, event in enumerate(native)
    ]


def _text(index: int, *pieces: str) -> list[Any]:
    return [
        PartStartEvent(index=index, part=TextPart("")),
        *(PartDeltaEvent(index=index, delta=TextPartDelta(content_delta=piece)) for piece in pieces),
        PartEndEvent(index=index, part=TextPart("".join(pieces))),
    ]


def _tool_call(index: int, call_id: str, *arguments: str) -> list[Any]:
    return [
        PartStartEvent(index=index, part=ToolCallPart(tool_name="search", args=None, tool_call_id=call_id)),
        *(
            PartDeltaEvent(index=index, delta=ToolCallPartDelta(args_delta=piece, tool_call_id=call_id))
            for piece in arguments
        ),
        PartEndEvent(index=index, part=ToolCallPart(tool_name="search", args="".join(arguments), tool_call_id=call_id)),
    ]


def _trimming(settings: Settings, window: float) -> Settings:
    return settings.model_copy(update={"worker": settings.worker.model_copy(update={"stream_trim_seconds": window})})


async def _entries(redis: Redis, thread_id: str) -> list[tuple[str, dict[str, str]]]:
    return await redis.xrange(stream_key(thread_id))


def _events(entries: list[tuple[str, dict[str, str]]]) -> list[dict[str, Any]]:
    return [json.loads(fields["event"]) for _, fields in entries if "event" in fields]


def _contents(events: list[dict[str, Any]], kind: str = "TEXT_MESSAGE_CONTENT") -> list[str]:
    return [event["delta"] for event in events if event["type"] == kind]


def _streamed_arguments(events: list[dict[str, Any]]) -> list[str]:
    return [event["value"]["event"]["delta"]["args_delta"] for event in events if event.get("name") == PART_DELTA]


def _argument_items(display: Display) -> list[str | None]:
    """The argument text of each observation item holding a tool call's streamed arguments; None once omitted."""
    texts: list[str | None] = []
    for item in display.items:
        if item.kind == "observation" and item.content["name"] == PART_DELTA:
            value: Any = item.content["value"]
            texts.append(None if value == {"omitted": True} else value["event"]["delta"]["args_delta"])
    return texts


def _replies(events: list[dict[str, Any]]) -> list[str]:
    """The streamed text deltas of assistant messages."""
    replies = {event["messageId"] for event in events if event["type"] == "TEXT_MESSAGE_START"}
    replies &= {event["messageId"] for event in events if event.get("role") == "assistant"}
    return [
        event["delta"] for event in events if event["type"] == "TEXT_MESSAGE_CONTENT" and event["messageId"] in replies
    ]


async def _coalesce(
    runtime: Runtime, sources: list[HarnessEvent], *, window: float, boundary_after: int | None = None
) -> tuple[list[tuple[str, dict[str, str]]], Display]:
    """Feed Harness events through a coalescer as an attempt does, committing a boundary after the event at
    `boundary_after`; the stream entries and the display it leaves."""
    thread_id = f"thr_{uuid4().hex}"
    fold = DisplayFold(RUN, Display(), attempt=1, max_bytes=runtime.settings.worker.display_bytes)
    live = ThreadStream(runtime.redis, runtime.settings, thread_id=thread_id, run_id=RUN, attempt=1)
    async with live, Coalescer(fold, live, window=window) as output:
        for index, source in enumerate(sources):
            output.observe(source)
            if index == boundary_after:
                output.flush()
                output.boundary()
            if index % 100 == 0:
                await live.buffer.join()  # A model streams slower than this loop: let the writer keep up.
    return await _entries(runtime.redis, thread_id), fold.snapshot()


def _positionless(display: Display) -> list[dict[str, Any]]:
    """Items without the stream positions they record, which merging renumbers; an observation's ID is its
    position."""
    return [
        item.model_dump(exclude={"first_stream_id", "last_stream_id", *(("id",) if item.kind == "observation" else ())})
        for item in display.items
    ]


# Coalescing


async def test_fragments_merge_and_fold_the_same_display(runtime: Runtime) -> None:
    thinking = [
        PartStartEvent(index=0, part=ThinkingPart("")),
        *(PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=piece)) for piece in ("Let ", "me ", "see")),
        PartEndEvent(index=0, part=ThinkingPart("Let me see", signature="sig")),
    ]
    sources = _harness(
        [
            *thinking,
            *_text(1, "Look", "ing ", "it ", "up"),
            *_tool_call(2, "call-1", '{"q":', '"x"}'),
            *_tool_call(3, "call-2", "{}"),
            FunctionToolResultEvent(ToolReturnPart(tool_name="search", content="found", tool_call_id="call-1")),
            FunctionToolResultEvent(RetryPromptPart("bad arguments", tool_name="search", tool_call_id="call-2")),
            *_text(4, "It ", "is ", "x"),
            # The same part index in a later model response is another part.
            *_tool_call(2, "call-3", '{"r":', "1}"),
        ]
    )
    # The boundary arrives while "Look" and "ing " are held.
    each, unmerged = await _coalesce(runtime, sources, window=0, boundary_after=7)
    merged_entries, merged = await _coalesce(runtime, sources, window=60, boundary_after=7)

    events = _events(merged_entries)
    assert _contents(events, "REASONING_MESSAGE_CONTENT") == ["Let me see"]
    assert _contents(events) == ["Looking ", "it up", "It is x"]
    assert len(_contents(_events(each))) == 7
    # A tool call's streamed arguments merge per part, as one observation item each.
    assert _streamed_arguments(_events(each)) == ['{"q":', '"x"}', "{}", '{"r":', "1}"]
    assert _streamed_arguments(events) == ['{"q":"x"}', "{}", '{"r":1}']
    assert _argument_items(merged) == ['{"q":"x"}', "{}", '{"r":1}']
    # Sequences stay dense, and the boundary covers every event observed before it.
    sequences = [int(fields["sequence"]) for _, fields in merged_entries]
    boundary = next(index for index, (_, fields) in enumerate(merged_entries) if "boundary" in fields)
    assert sequences[:boundary] + sequences[boundary + 1 :] == list(range(1, len(merged_entries)))
    assert sequences[boundary] == sequences[boundary - 1]
    assert merged.position.sequence == len(merged_entries) - 1 < unmerged.position.sequence
    assert _positionless(merged) == _positionless(unmerged)
    failed = [item for item in merged.items if item.kind == "tool_call" and item.state == "failed"]
    assert [item.content["toolCallId"] for item in failed] == ["call-2"]
    assert [item.content.get("encrypted_value") for item in merged.items if item.kind == "reasoning_message"] == ["sig"]


async def test_a_long_streamed_tool_call_is_one_item_and_few_entries(runtime: Runtime) -> None:
    pieces = ["tok "] * 3000
    # Arguments beyond the observation limit keep the one item, without its value.
    oversized = ["x" * 20000] * 2
    sources = _harness([*_tool_call(0, "call-1", *pieces), *_tool_call(1, "call-2", *oversized)])
    each, unmerged = await _coalesce(runtime, sources, window=0)
    merged_entries, merged = await _coalesce(runtime, sources, window=60)

    assert len(_streamed_arguments(_events(each))) == 3002
    assert len(_streamed_arguments(_events(merged_entries))) == 4
    assert "".join(_streamed_arguments(_events(merged_entries))[:2]) == "".join(pieces)
    assert _argument_items(merged) == ["".join(pieces), None]
    assert _positionless(merged) == _positionless(unmerged)
    # Per tool call: its start observation, its argument observation and the call.
    assert len(unmerged.items) == len(merged.items) == 6


async def test_a_merged_fragment_stays_within_the_bound(runtime: Runtime) -> None:
    long = "x" * 5000
    entries, _ = await _coalesce(runtime, _harness(_text(0, long, long, long, "y", "z")), window=60)

    assert [len(delta) for delta in _contents(_events(entries))] == [5000, 5000, 5002]
    assert all(len(delta) <= MAX_MERGED_CHARS for delta in _contents(_events(entries)))


async def test_a_pause_releases_held_text_within_the_window(runtime: Runtime) -> None:
    thread_id = f"thr_{uuid4().hex}"
    start, hel, lo, end = _harness(_text(0, "Hel", "lo"))
    fold = DisplayFold(RUN, Display(), attempt=1, max_bytes=runtime.settings.worker.display_bytes)
    live = ThreadStream(runtime.redis, runtime.settings, thread_id=thread_id, run_id=RUN, attempt=1)
    async with live, Coalescer(fold, live, window=0.05) as output:
        output.observe(start)
        output.observe(hel)
        async with asyncio.timeout(5):
            while not _contents(_events(await _entries(runtime.redis, thread_id))):
                await asyncio.sleep(0.02)
        output.observe(lo)
        output.observe(end)

    assert _contents(_events(await _entries(runtime.redis, thread_id))) == ["Hel", "lo"]


async def test_the_attempt_end_releases_a_held_fragment(runtime: Runtime) -> None:
    for ending in (None, asyncio.CancelledError):
        thread_id = f"thr_{uuid4().hex}"
        start, hello, _ = _harness(_text(0, "Hello"))
        fold = DisplayFold(RUN, Display(), attempt=1, max_bytes=runtime.settings.worker.display_bytes)
        live = ThreadStream(runtime.redis, runtime.settings, thread_id=thread_id, run_id=RUN, attempt=1)
        try:
            async with live, Coalescer(fold, live, window=60) as output:
                output.observe(start)
                output.observe(hello)
                if ending is not None:
                    raise ending
        except asyncio.CancelledError:
            pass

        assert _contents(_events(await _entries(runtime.redis, thread_id))) == ["Hello"], ending
        assert fold.snapshot().items[0].content["text"] == "Hello"


async def test_a_streamed_reply_reaches_the_stream_coalesced(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    pieces = ["tok "] * 400
    scripted_model.say(*pieces)
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "fast"))["run"]["id"])

    entries = await _entries(executing.runtime.redis, run["thread_id"])
    replies = _replies(_events(entries))
    assert "".join(replies) == "".join(pieces)
    assert len(replies) <= len(pieces) // 10, replies
    listing = await runs_kit.items(executing, run["id"])
    assert runs_kit.texts(listing) == [("user", "fast"), ("assistant", "".join(pieces))]
    last_id, last_fields = [(entry_id, fields) for entry_id, fields in entries if "boundary" not in fields][-1]
    assert listing["resume_after"] == last_id
    assert listing["position"] == f"{last_fields['attempt']}-{last_fields['sequence']}"
    deltas = [int(fields["sequence"]) for _, fields in entries if "boundary" not in fields]
    assert deltas == list(range(1, len(deltas) + 1))

    # Text the model pauses in the middle of reaches readers within the window instead of waiting for the rest.
    scripted_model.say("Hello", " world", interval=0.5)
    paused = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "slow"))["run"]["id"])
    assert _replies(_events(await _entries(executing.runtime.redis, paused["thread_id"]))) == ["Hello", " world"]


# Trimming


def _delta(sequence: int) -> Observed:
    return Observed(
        sequence=sequence, event={"type": "TEXT_MESSAGE_CONTENT", "messageId": "msg", "delta": str(sequence)}, item=None
    )


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
    assert _replies(_events(entries)) == ["Kept"]


async def _queued_run(service, scripted_model, runs_kit) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A thread whose run waits for a worker the control role does not run: the stream's current run."""
    agent = await runs_kit.create_agent(service, scripted_model)
    return (await runs_kit.start_thread(service, agent, "hi"))["run"]


async def _frames(stream: Any, count: int) -> list[tuple[str, Any]]:
    async with asyncio.timeout(10):
        return [(event, data.get("sequence")) for event, _, data in [await anext(stream) for _ in range(count)]]


async def test_readers_after_a_trim_receive_the_tail_and_gaps(service, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]
    run = await _queued_run(service, scripted_model, runs_kit)
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
        async with runs_kit.frames(url, {**headers, "last-event-id": boundary_id}) as stream:
            assert await _frames(stream, 1) == [("delta", 3)]
        # A reader whose position was removed starts with a gap.
        async with runs_kit.frames(url, {**headers, "last-event-id": "1-0"}) as stream:
            assert (await _frames(stream, 1))[0] == ("gap", None)


async def test_a_live_reader_skipped_past_removed_entries_gets_a_gap(service, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]
    run = await _queued_run(service, scripted_model, runs_kit)
    redis, key = service.runtime.redis, stream_key(run["thread_id"])
    fields = {"run_id": run["id"], "attempt": "1"}

    def delta(sequence: int) -> dict[str, str]:
        return {**fields, "sequence": str(sequence), "event": json.dumps(_delta(sequence).event)}

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
                "event": json.dumps(_delta(sequence).event),
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
            "event": json.dumps(_delta(151).event),
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
        key, {"run_id": run["id"], "attempt": "1", "sequence": "100", "event": json.dumps(_delta(100).event)}
    )
    await redis.xadd(key, {"run_id": run["id"], "attempt": "2", "sequence": "1", "event": json.dumps(_delta(1).event)})
    # A fenced-out worker can finish an old Redis write after the new attempt starts.
    cursor = await redis.xadd(
        key, {"run_id": run["id"], "attempt": "1", "sequence": "100", "event": json.dumps(_delta(100).event)}
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
