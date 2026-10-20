"""Thread output in Redis, coalesced and trimmed at boundaries, and the session stream that reads it."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from a13n_harness import HarnessEvent
from a13n_service.runs import stream as stream_module
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.claim import claim
from a13n_service.runs.coalesce import MAX_MERGED_CHARS, Coalescer
from a13n_service.runs.display import DisplayFold, Observed, Tail
from a13n_service.runs.execute import execute
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.seal import release_attempt
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


def _argument_items(display: Tail) -> list[str | None]:
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
) -> tuple[list[tuple[str, dict[str, str]]], Tail]:
    """Feed Harness events through a coalescer as an attempt does, committing a boundary after the event at
    `boundary_after`; the stream entries and the display it leaves."""
    thread_id = f"thr_{uuid4().hex}"
    fold = DisplayFold(RUN, Tail(), attempt=1, page_items=256, page_bytes=1048576)
    live = ThreadStream(runtime.redis, runtime.settings, thread_id=thread_id, run_id=RUN, attempt=1)
    async with live, Coalescer(fold, live, window=window) as output:
        for index, source in enumerate(sources):
            output.observe(source)
            if index == boundary_after:
                output.flush()
                output.boundary()
            if index % 100 == 0:
                await live.buffer.join()  # A model streams slower than this loop: let the writer keep up.
    return await _entries(runtime.redis, thread_id), fold.snapshot().tail


def _positionless(display: Tail) -> list[dict[str, Any]]:
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
    # Service retains the full observation for external storage; transport coalescing stays unchanged.
    oversized = ["x" * 20000] * 2
    sources = _harness([*_tool_call(0, "call-1", *pieces), *_tool_call(1, "call-2", *oversized)])
    each, unmerged = await _coalesce(runtime, sources, window=0)
    merged_entries, merged = await _coalesce(runtime, sources, window=60)

    assert len(_streamed_arguments(_events(each))) == 3002
    assert len(_streamed_arguments(_events(merged_entries))) == 4
    assert "".join(_streamed_arguments(_events(merged_entries))[:2]) == "".join(pieces)
    assert _argument_items(merged) == ["".join(pieces), "".join(oversized)]
    assert _positionless(merged) == _positionless(unmerged)
    # Per tool call: its start observation, its argument observation and the call.
    assert len(unmerged.items) == len(merged.items) == 6


async def test_a_merged_fragment_stays_within_the_bound(runtime: Runtime) -> None:
    long = "x" * 5000
    entries, _ = await _coalesce(runtime, _harness(_text(0, long, long, long, "y", "z")), window=60)

    assert [len(delta) for delta in _contents(_events(entries))] == [5000, 5000, 5002]
    assert all(len(delta) <= MAX_MERGED_CHARS for delta in _contents(_events(entries)))


async def test_a_pause_releases_held_text_within_the_window(runtime: Runtime, runs_kit) -> None:  # type: ignore[no-untyped-def]
    thread_id = f"thr_{uuid4().hex}"
    start, hel, lo, end = _harness(_text(0, "Hel", "lo"))
    fold = DisplayFold(RUN, Tail(), attempt=1, page_items=256, page_bytes=1048576)
    live = ThreadStream(runtime.redis, runtime.settings, thread_id=thread_id, run_id=RUN, attempt=1)
    async with live, Coalescer(fold, live, window=0.05) as output:
        output.observe(start)
        output.observe(hel)
        await runs_kit.streamed_text(runtime, thread_id, "Hel")
        output.observe(lo)
        output.observe(end)

    assert _contents(_events(await _entries(runtime.redis, thread_id))) == ["Hel", "lo"]


async def test_the_attempt_end_releases_a_held_fragment(runtime: Runtime) -> None:
    for ending in (None, asyncio.CancelledError):
        thread_id = f"thr_{uuid4().hex}"
        start, hello, _ = _harness(_text(0, "Hello"))
        fold = DisplayFold(RUN, Tail(), attempt=1, page_items=256, page_bytes=1048576)
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
        assert fold.snapshot().tail.items[0].content["text"] == "Hello"


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
    display = await runs_kit.tail(executing, run["id"])
    assert display.resume_after == last_id
    assert str(display.position) == f"{last_fields['attempt']}-{last_fields['sequence']}"
    deltas = [int(fields["sequence"]) for _, fields in entries if "boundary" not in fields]
    assert deltas == list(range(1, len(deltas) + 1))

    # Text the model pauses in the middle of reaches readers within the window instead of waiting for the rest.
    next_chunk = asyncio.Event()
    scripted_model.say("Hello", " world", chunk_gate=next_chunk)
    paused = (await runs_kit.start_thread(executing, agent, "slow"))["run"]
    try:
        await runs_kit.streamed_text(executing.runtime, paused["thread_id"], "Hello")
        assert (await runs_kit.get_run(executing, paused["id"]))["status"] == "running"
    finally:
        next_chunk.set()
    await runs_kit.sealed(executing, paused["id"])
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
                display = await runs_kit.tail(service, run_id)
            assert not release.is_set()
            assert display.position.sequence > 0 and not listing["complete"]
            assert display.resume_after is None
            assert runs_kit.texts(listing) == [("user", "hi")]
        finally:
            release.set()
            gate.set()
            await task
        assert (await runs_kit.tail(service, run_id)).resume_after is not None


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
    display = await runs_kit.tail(service, run_id)
    assert display.resume_after == last_id
    assert int(fields["sequence"]) < display.position.sequence


async def test_a_boundary_trims_what_its_display_covers(runtime: Runtime) -> None:
    thread_id = f"thr_{uuid4().hex}"
    async with ThreadStream(
        runtime.redis, _trimming(runtime.settings, 0), thread_id=thread_id, run_id=RUN, attempt=1
    ) as live:
        live.delta(_delta(1))
        live.delta(_delta(2))
        await live.buffer.join()
        assert live.last_written is not None
        live.boundary(2, resume_after=live.last_written.redis_id)
        live.delta(_delta(3))
    assert [(fields["sequence"], "boundary" in fields) for _, fields in await _entries(runtime.redis, thread_id)] == [
        ("2", False),
        ("2", True),
        ("3", False),
    ]

    # Redis IDs date the retention window. Seed entries on both sides of the cutoff without waiting for age.
    thread_id = f"thr_{uuid4().hex}"
    for sequence, entry_id in [(1, "1000-0"), (2, "1400-0"), (3, "1600-0")]:
        await runtime.redis.xadd(
            stream_key(thread_id),
            {"run_id": RUN, "attempt": "1", "sequence": str(sequence), "event": json.dumps(_delta(sequence).event)},
            id=entry_id,
        )
    async with ThreadStream(
        runtime.redis, _trimming(runtime.settings, 0.3), thread_id=thread_id, run_id=RUN, attempt=1
    ) as live:
        live.boundary(3, resume_after="1600-0")
        live.delta(_delta(4))
    assert [(fields["sequence"], "boundary" in fields) for _, fields in await _entries(runtime.redis, thread_id)] == [
        ("2", False),
        ("3", False),
        ("3", True),
        ("4", False),
    ]


async def test_a_run_keeps_only_the_tail_after_its_latest_boundary(serve, settings, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    worker = settings.worker.model_copy(update={"scan_seconds": 0.05, "authority_seconds": 0.05})
    fast = _trimming(settings.model_copy(update={"worker": worker}), 0)
    async with serve(settings=fast, role="all") as service:
        agent = await runs_kit.create_agent(service, scripted_model)
        scripted_model.say("Kept")
        run = await runs_kit.sealed(service, (await runs_kit.start_thread(service, agent, "Removed"))["run"]["id"])
        entries = await _entries(service.runtime.redis, run["thread_id"])
        listing = await runs_kit.items(service, run["id"])

    # The first retained entry is the conservative confirmed anchor, never a
    # late commit marker. A lagging/missing anchor may retain additional output.
    markers = [fields for _, fields in entries if "resume_after" in fields]
    if markers:
        assert entries[0][0] == markers[-1]["resume_after"]
    assert runs_kit.texts(listing) == [("user", "Removed"), ("assistant", "Kept")]


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


async def test_final_display_does_not_need_redis(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("Saved")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "hi"))["run"]["id"])
    before = await runs_kit.items(executing, run["id"])
    await executing.runtime.redis.delete(stream_key(run["thread_id"]))
    assert await runs_kit.items(executing, run["id"]) == before


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


async def test_display_retains_standard_interrupt_and_scopes_child_native_ids() -> None:
    fold = DisplayFold(RUN, Tail(), attempt=1, page_items=256, page_bytes=1048576)
    events = [
        {"type": "TOOL_CALL_START", "toolCallId": "same", "toolCallName": "root"},
        {"type": "TOOL_CALL_START", "toolCallId": "same", "toolCallName": "child", "subagentRunId": "child"},
        {
            "type": "TOOL_CALL_RESULT",
            "toolCallId": "same",
            "messageId": "result",
            "content": [{"type": "image", "source": {"type": "url", "value": "https://example.com/a.png"}}],
            "subagentRunId": "child",
        },
        {
            "type": "RUN_FINISHED",
            "runId": "harness",
            "threadId": "thread",
            "outcome": {
                "type": "interrupt",
                "interrupts": [{"id": "same", "toolCallId": "same", "reason": "approval"}],
            },
        },
    ]
    observed = fold.fold(events)
    assert observed[-1].item is not None
    # The root call is the primary run's open one; the child's call of the same ID is another item.
    fold.interrupt(open_calls={"same"})
    saved = fold.snapshot().tail
    root, child, interrupt = saved.items
    assert root.id != child.id and root.state == "in_progress" and child.state == "completed"
    assert child.content["subagentRunId"] == "child"
    assert child.content["result_parts"] == events[2]["content"]
    assert interrupt.content == events[-1]
    restored = DisplayFold(
        RUN, Tail.model_validate_json(saved.model_dump_json()), attempt=2, page_items=256, page_bytes=1048576
    )
    assert restored.snapshot().tail.items[-1].content == interrupt.content


@pytest.mark.parametrize("hint", [True, False])
async def test_late_checkpoint_marker_never_trims_uncovered_deltas(runtime: Runtime, hint: bool) -> None:
    thread_id = f"thr_{uuid4().hex}"
    async with ThreadStream(
        runtime.redis, _trimming(runtime.settings, 0), thread_id=thread_id, run_id=RUN, attempt=1
    ) as live:
        for sequence in range(1, 4):
            live.delta(_delta(sequence))
        await live.buffer.join()
        assert live.last_written is not None
        frozen_hint = live.last_written.redis_id if hint else None
        # Freeze at 4 with a lagging confirmed hint at 3. Persistence yields;
        # 4, 5 and 6 are written before the checkpoint's commit marker.
        for sequence in range(4, 7):
            live.delta(_delta(sequence))
        await live.buffer.join()
        live.boundary(4, resume_after=frozen_hint)
    retained = [
        int(fields["sequence"]) for _, fields in await _entries(runtime.redis, thread_id) if "boundary" not in fields
    ]
    assert retained == (list(range(3, 7)) if hint else list(range(1, 7)))


# Reading


def _entry(run_id: str, sequence: int, *, attempt: int = 1, **fields: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "attempt": str(attempt),
        "sequence": str(sequence),
        "event": '{"type":"TEXT_MESSAGE_CONTENT","messageId":"m","delta":"x"}',
        "item": "null",
        **fields,
    }


def _summary(frames: list[Any]) -> list[tuple[str, Any]]:
    """Each frame's event and what tells it apart: a thread's ID, a run's base ordinal or a delta's position."""
    described = {
        "thread": lambda data: data["thread"]["id"],
        "items": lambda data: (data["run_id"], data["first"], data["complete"]),
        "delta": lambda data: (data["run_id"], data["attempt"], data["sequence"]),
        "gap": lambda data: data["run_id"],
    }
    return [(event, described[event](data)) for event, _, data in frames]


async def test_a_session_stream_starts_with_each_thread_and_streams_its_active_runs(
    service, scripted_model, listen, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    first = await runs_kit.start_thread(service, agent, "first")
    second = await runs_kit.start_thread(service, agent, "second", session_id=first["thread"]["session_id"])
    run, key = first["run"]["id"], stream_key(first["thread"]["id"])
    redis = service.runtime.redis
    assert len(await claim(service.runtime, worker_id="worker", worker_build="test", limit=2)) == 2
    # Retained output of the active run reaches a new reader after its run's items.
    await redis.xadd(key, _entry(run, 1))
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{first['thread']['session_id']}/stream"
        async with runs_kit.frames(url, headers) as stream:
            opened = await runs_kit.until(stream, lambda frame: frame[0] == "items" and frame[2]["run_id"] != run)
            assert _summary(opened) == [
                ("thread", first["thread"]["id"]),
                ("items", (run, 1, False)),
                ("delta", (run, 1, 1)),
                ("thread", second["thread"]["id"]),
                ("items", (second["run"]["id"], 1, False)),
            ]
            state = opened[0][2]
            assert state["current_run"]["id"] == run and state["last_run"] is None
            assert [entry["id"] for entry in state["entries"]] == [first["entry"]["id"]]
            assert "message_history" not in state["thread"]
            assert opened[2][2]["event"]["type"] == "TEXT_MESSAGE_CONTENT"

            # Live output follows in order; a skipped sequence holds the run's output.
            await redis.xadd(key, _entry(run, 2))
            await redis.xadd(key, _entry(run, 4))
            await redis.xadd(key, _entry(run, 5))
            await redis.xadd(stream_key(second["thread"]["id"]), _entry(second["run"]["id"], 1))
            live = await runs_kit.until(stream, lambda frame: frame[0] == "delta" and frame[2]["run_id"] != run)
            assert _summary(live) == [("delta", (run, 1, 2)), ("gap", run), ("delta", (second["run"]["id"], 1, 1))]


async def test_lost_output_is_held_until_a_checkpoint_covers_it(
    service, scripted_model, listen, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    gate = asyncio.Event()
    scripted_model.say("Recovered", gate=gate)
    submitted = await runs_kit.start_thread(service, agent, "hi")
    run = submitted["run"]["id"]
    read = stream_module.read
    dropped: list[str] = []

    async def lossy(*args: Any, **kwargs: Any) -> list[Any]:
        # The shared read misses the run's second delta, as a reader whose entries were trimmed does.
        entries = await read(*args, **kwargs)
        lost = [entry for entry in entries if entry.fields.get("sequence") == "2" and "boundary" not in entry.fields]
        dropped.extend(entry.id for entry in lost[:1])
        return [entry for entry in entries if entry.id not in dropped]

    monkeypatch.setattr(stream_module, "read", lossy)
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{submitted['thread']['session_id']}/stream"
        (lease,) = await claim(service.runtime, worker_id="worker", worker_build="test", limit=1)
        async with runs_kit.frames(url, headers) as stream:
            # The stream follows the claimed attempt before its worker writes anything.
            await runs_kit.until(stream, lambda frame: frame[0] == "items")
            deadline = asyncio.get_running_loop().time() + service.runtime.settings.worker.lease_seconds
            task = asyncio.create_task(execute(service.runtime, lease, AttemptControl(deadline=deadline)))
            try:
                held = await runs_kit.until(stream, lambda frame: frame[0] == "gap")
                assert _summary(held)[-2:] == [("delta", (run, 1, 1)), ("gap", run)]
                # Nothing of the run arrives until a checkpoint covers the loss and the stream sends its items again.
                repaired = await runs_kit.until(stream, lambda frame: frame[0] == "items")
                assert [event for event, _, _ in repaired] in (["items"], ["thread", "items"])
                assert repaired[-1][2]["first"] == 1 and not repaired[-1][2]["complete"]
                assert runs_kit.texts(repaired[-1][2]) == [("user", "hi")]
                gate.set()
                sealed = await runs_kit.until(stream, lambda frame: frame[0] == "items" and frame[2]["complete"])
            finally:
                gate.set()
                await task
    assert dropped
    # Sealing sends the thread, then the run's final items.
    thread, final = sealed[-2][2], sealed[-1][2]
    assert sealed[-2][0] == "thread" and thread["current_run"] is None
    assert thread["last_run"]["id"] == run and thread["last_run"]["status"] == "completed"
    assert runs_kit.texts(final) == [("user", "hi"), ("assistant", "Recovered")]
    assert any(event == "delta" and data["event"].get("delta") == "Recovered" for event, _, data in sealed), (
        "live output resumes after the repair"
    )

    # A new connection starts from the current state: the thread, and no items of a sealed run.
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{submitted['thread']['session_id']}/stream"
        async with runs_kit.frames(url, headers) as stream:
            event, _, state = await anext(stream)
            assert event == "thread" and state["current_run"] is None and state["last_run"]["id"] == run
            # A later thread snapshot is an ordered barrier: sealed-run items must not precede it.
            resumed = await runs_kit.submit(service, submitted["thread"]["id"], runs_kit.message(agent, "next turn"))
            assert resumed.status_code == 201, resumed.text
            event, _, state = await anext(stream)
            assert event == "thread" and state["current_run"] is not None
            assert state["current_run"]["id"] != run


async def test_a_new_attempt_restarts_its_runs_output(service, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]

    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "hi")
    run, key = submitted["run"]["id"], stream_key(submitted["thread"]["id"])
    redis = service.runtime.redis
    first = (await claim(service.runtime, worker_id="first", worker_build="test", limit=1))[0]
    headers = await runs_kit.bearer(service)
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{submitted['thread']['session_id']}/stream"
        async with runs_kit.frames(url, headers) as stream:
            await runs_kit.until(stream, lambda frame: frame[0] == "items")
            await redis.xadd(key, _entry(run, 1))
            assert _summary(await runs_kit.until(stream, lambda frame: frame[0] == "delta")) == [("delta", (run, 1, 1))]
            await release_attempt(service.runtime, first, status="yielded", yield_reason="handoff")
            second = (await claim(service.runtime, worker_id="second", worker_build="test", limit=1))[0]
            assert second.number == 2
            # The new attempt's output restarts the run from its items.
            await redis.xadd(key, _entry(run, 1, attempt=2))
            restarted = await runs_kit.until(stream, lambda frame: frame[0] == "delta")
            assert [frame for frame in _summary(restarted) if frame[0] != "thread"] == [
                ("items", (run, 1, False)),
                ("delta", (run, 2, 1)),
            ]
            # A fenced-out worker can finish an old write after the new attempt started.
            await redis.xadd(key, _entry(run, 2))
            await redis.xadd(key, _entry(run, 2, attempt=2))
            later = await runs_kit.until(stream, lambda frame: frame[0] == "delta")
            assert [frame for frame in _summary(later) if frame[0] != "thread"] == [("delta", (run, 2, 2))]


async def test_a_session_stream_ends_when_its_reader_loses_access(service, scripted_model, listen, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "hi")
    issued = await service.client.post(
        "/api/v1/users/me/keys", json={"name": "stream", "workspace_id": service.tenant.workspace_id}
    )
    assert issued.status_code == 201, issued.text
    key = issued.json()["key"]
    async with listen(service.app) as base:
        url = f"{base}{service.api}/sessions/{submitted['thread']['session_id']}/stream"
        async with runs_kit.frames(url, {"authorization": f"Bearer {issued.json()['secret']}"}) as stream:
            await runs_kit.until(stream, lambda frame: frame[0] == "items")
            revoked = await service.client.delete(f"/api/v1/users/me/keys/{key['id']}", headers=runs_kit.if_match(key))
            assert revoked.status_code == 200, revoked.text
            async with asyncio.timeout(10):
                assert [frame async for frame in stream] == []
    # A session outside the workspace is not found before any frame.
    missing = await service.client.get(f"{service.api}/sessions/ses_missing/stream")
    assert missing.status_code == 404, missing.text


def _connection() -> tuple[Any, Any]:
    from types import SimpleNamespace

    from a13n_service.runs.stream import Reader, _Connection, _Follow

    hub = SimpleNamespace(nudges=0)
    hub.nudge = lambda: setattr(hub, "nudges", hub.nudges + 1)
    connection = _Connection(hub, Reader(credential=None, workspace_id="workspace", session_id="session"))  # type: ignore[arg-type]
    connection.follows["run_a"] = _Follow("thread", attempt=2, sequence=0, first=1)
    connection.follows["run_b"] = _Follow("thread", attempt=1, sequence=3, first=1)
    return connection, hub


def _stream_entry(run_id: str, sequence: int, **fields: Any) -> Any:
    from a13n_service.infra.redis import StreamEntry

    return StreamEntry(key=stream_key("thread"), id=f"1-{sequence}", fields=_entry(run_id, sequence, **fields))


def _names(frames: list[str]) -> list[str]:
    return [frame.split("\n", 1)[0].removeprefix("event: ") for frame in frames]


async def test_a_connection_holds_a_run_from_its_first_loss() -> None:
    connection, hub = _connection()
    deliver = connection.entry
    assert _names(deliver(_stream_entry("run_a", 1, attempt=2))) == ["delta"]
    # An older attempt is fenced out; a newer one asks for a refresh once.
    assert deliver(_stream_entry("run_a", 2, attempt=1)) == []
    assert deliver(_stream_entry("run_a", 1, attempt=3)) == deliver(_stream_entry("run_a", 2, attempt=3)) == []
    assert hub.nudges == 1
    # A skipped sequence reports one gap and holds everything after it.
    assert _names(deliver(_stream_entry("run_a", 3, attempt=2))) == ["gap"]
    assert deliver(_stream_entry("run_a", 4, attempt=2)) == []
    assert connection.follows["run_a"].held == 2
    # A boundary past the delivered output covers deltas that never arrived.
    assert _names(deliver(_stream_entry("run_b", 5, boundary="1"))) == ["gap"]
    assert connection.follows["run_b"].held == 5
    # Output of a run the connection does not follow asks for a refresh.
    assert deliver(_stream_entry("run_c", 1)) == []
    assert hub.nudges == 2


def _refresh(run_id: str, sequence: int, first: int) -> Any:
    from a13n_service.runs.display import StreamPosition
    from a13n_service.runs.stream import Progress, Update

    return Update("thread", None, Progress(run_id, 1, StreamPosition(attempt=1, sequence=sequence), first), None)


async def test_a_refresh_sends_items_again_for_missing_output_and_final_previews() -> None:
    from a13n_service.runs.stream import RunSnapshot, _Base

    connection, hub = _connection()
    del connection.follows["run_a"]
    hub.bases = []

    async def base(run_id: str, first: int) -> _Base:
        hub.bases.append(first)
        snapshot = RunSnapshot(
            thread_id="thread", run_id=run_id, first=first, items=[], continuation=None, complete=False
        )
        # Redis retains nothing after the base up to the connection's position.
        return _Base(snapshot, attempt=1, sequence=7, first=first, resume_after="1-9", preview=4)

    hub.base = base
    # Output committed ahead of delivery is expected by the next refresh, and arriving in time is enough.
    assert await connection.update(_refresh("run_b", 5, first=1)) == []
    assert _names(connection.entry(_stream_entry("run_b", 4)) + connection.entry(_stream_entry("run_b", 5))) == [
        "delta",
        "delta",
    ]
    assert await connection.update(_refresh("run_b", 5, first=3)) == []
    assert connection.follows["run_b"].first == 3 and hub.bases == []
    # Output still missing a refresh later was lost: a gap, then items from the first item not received final.
    assert await connection.update(_refresh("run_b", 7, first=3)) == []
    assert _names(await connection.update(_refresh("run_b", 7, first=3))) == ["gap", "items"]
    assert hub.bases == [3]
    # That base sent item 4 as a preview of a large field: it is sent again once final.
    assert await connection.update(_refresh("run_b", 7, first=4)) == []
    assert _names(await connection.update(_refresh("run_b", 7, first=5))) == ["items"]
    assert hub.bases == [3, 4]


async def test_a_reader_too_far_behind_is_closed() -> None:
    from a13n_service.runs.stream import READ_BYTES, Close, Reader

    reader = Reader(credential=None, workspace_id="workspace", session_id="session")  # type: ignore[arg-type]
    reader.send(_stream_entry("run_a", 1))
    reader.send(_stream_entry("run_a", 2, event="x" * READ_BYTES))
    reader.send(_stream_entry("run_a", 3))
    assert reader.closed and reader.signals.qsize() == 2
    assert (await reader.receive()).id == "1-1" and isinstance(await reader.receive(), Close)
