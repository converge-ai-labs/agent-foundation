"""Complete display values, unchanged-reference reuse, authorized reads and orphan reclamation."""

from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import Any

import pytest
from a13n_harness import HarnessEvent
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.objects.interface import reference
from a13n_service.infra.outbox import Delivery
from a13n_service.runs import checkpoints
from a13n_service.runs import contents as display_contents
from a13n_service.runs.attempts import Lease
from a13n_service.runs.coalesce import Coalescer
from a13n_service.runs.contents import INLINE_BYTES, MAX_CONTENT_BYTES, PREVIEW_BYTES, Contents, decode
from a13n_service.runs.display import DisplayFold, Item, Page, Snapshot, StreamPosition, Tail
from a13n_service.runs.tables import RunItemPageRow, RunRow
from a13n_stream_protocol.display import DisplayFold as SemanticFold
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)
from sqlalchemy import select

pytestmark = pytest.mark.anyio
AT = datetime(2026, 10, 8, tzinfo=UTC)


def item(value: Any, field: str = "text", **changes: Any) -> Item:
    return Item(
        id="itm_one",
        ordinal=1,
        kind="text_message",
        state="completed",
        first_stream_id="1-1",
        last_stream_id="1-1",
        started_at=AT,
        ended_at=AT,
        content={field: value},
        **changes,
    )


@pytest.mark.parametrize("size", [INLINE_BYTES - 1, INLINE_BYTES, INLINE_BYTES + 1])
async def test_threshold_counts_utf8_bytes_and_keeps_complete_values(size: int) -> None:
    text = "中" * (size // 3) + "x" * (size % 3)
    writes: list[bytes] = []

    async def write(data: bytes):
        writes.append(data)
        return reference(f"body/{len(writes)}", data, "application/zstd")

    stored = await Contents().item(item(text), write)
    if size <= INLINE_BYTES:
        assert stored.content["text"] == text
        assert not stored.content_refs and not writes
    else:
        assert writes == [text.encode()]
        assert len(str(stored.content["text"]).encode()) <= PREVIEW_BYTES
        assert stored.content_refs["text"].size_bytes == size
        assert "truncated" not in stored.content


@pytest.mark.parametrize("size", [MAX_CONTENT_BYTES - 1, MAX_CONTENT_BYTES, MAX_CONTENT_BYTES + 1])
async def test_body_limit_applies_before_compression(size: int) -> None:
    writes: list[bytes] = []

    async def write(data: bytes):
        writes.append(data)
        return reference(f"body/{len(writes)}", data, "application/zstd")

    contents = Contents()
    stored = await contents.item(item("x" * size), write)
    ref = stored.content_refs["text"]
    assert writes == [b"x" * min(size, MAX_CONTENT_BYTES)]
    assert ref.size_bytes == min(size, MAX_CONTENT_BYTES)
    assert ref.truncated is (size > MAX_CONTENT_BYTES)
    assert decode(writes[0], contents.refs[ref.id]) == "x" * min(size, MAX_CONTENT_BYTES)


async def test_utf8_truncated_prefix_survives_restore_and_later_suffix(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    lease = Lease("run_body", "rat_body", "thr_body", tenant.organization_id, tenant.workspace_id, 1, "w", "t")
    original = "中" * (MAX_CONTENT_BYTES // 3 + 1)
    first = await checkpoints.publish_display(runtime, lease, Snapshot([], Tail(items=[item(original)])))
    ref = first.tail.refs[0]
    assert ref.truncated and ref.size_bytes == MAX_CONTENT_BYTES - 1
    restored = await checkpoints.load_tail(runtime.objects, first.tail, hydrate=True)
    prefix = restored.items[0].content["text"]
    assert prefix == original[:-1]
    contents = Contents(restored.items, restored.refs)
    # A later ASCII suffix must not occupy the byte left at the UTF-8 boundary after missing content.
    restored.items[0].content["text"] += "suffix"
    second = await checkpoints.publish_display(runtime, lease, Snapshot([], restored), contents=contents)
    assert second.tail.refs == (ref,)
    assert (await checkpoints.load_tail(runtime.objects, second.tail, hydrate=True)).items[0].content["text"] == prefix


async def test_oversized_json_is_saved_as_an_explicit_truncated_text_prefix() -> None:
    writes: list[bytes] = []

    async def write(data: bytes):
        writes.append(data)
        return reference(f"body/{len(writes)}", data, "application/zstd")

    contents = Contents()
    stored = await contents.item(item({"text": "x" * MAX_CONTENT_BYTES}, "value"), write)
    ref = stored.content_refs["value"]
    assert ref.truncated and ref.media_type == "text/plain"
    assert ref.size_bytes == MAX_CONTENT_BYTES
    assert decode(writes[0], contents.refs[ref.id]) == '{"text":"' + "x" * (MAX_CONTENT_BYTES - 9)


async def test_crossing_limit_changes_metadata_even_if_saved_bytes_are_unchanged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(display_contents, "MAX_CONTENT_BYTES", INLINE_BYTES + 1)
    writes: list[bytes] = []

    async def write(data: bytes):
        writes.append(data)
        return reference(f"body/{len(writes)}", data, "application/zstd")

    contents = Contents()
    full = await contents.item(item("x" * (INLINE_BYTES + 1)), write)
    cut = await contents.item(item("x" * (INLINE_BYTES + 2)), write)
    assert not full.content_refs["text"].truncated
    assert cut.content_refs["text"].truncated
    assert cut.content_refs["text"].id != full.content_refs["text"].id
    assert writes[0] == writes[1]


async def test_unchanged_fields_reuse_references_and_changed_values_are_new_objects(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    lease = Lease("run_body", "rat_body", "thr_body", tenant.organization_id, tenant.workspace_id, 1, "w", "t")
    original = item("中" * 20000)
    snapshot = Snapshot([], Tail(items=[original], position=StreamPosition(attempt=1, sequence=1)))
    contents = Contents()
    first = await checkpoints.publish_display(runtime, lease, snapshot, contents=contents)
    second = await checkpoints.publish_display(runtime, lease, snapshot, contents=contents)
    assert first.tail.key != second.tail.key
    assert first.tail.refs == second.tail.refs
    ref = first.tail.refs[0]
    restored = await checkpoints.load_tail(runtime.objects, first.tail, hydrate=True)
    assert restored.items[0].content == original.content
    copied = restored.items[0].model_copy(update={"id": "itm_two", "ordinal": 2})
    restored.items.append(copied)
    resumed = Contents(restored.items, restored.refs)
    # Moving items into a page preserves both their shared reference and their complete bytes.
    paged = await checkpoints.publish_display(
        runtime, lease, Snapshot([Page(items=restored.items)], Tail(first=3)), contents=resumed
    )
    assert paged.pages[0].refs == (ref,)
    page = await checkpoints.load_page(runtime.objects, paged.pages[0])
    assert page.items[0].content_refs == page.items[1].content_refs
    changed = original.model_copy(update={"content": {"text": original.content["text"] + "more"}})
    grown = await checkpoints.publish_display(runtime, lease, Snapshot([], Tail(items=[changed])), contents=contents)
    assert grown.tail.refs[0].key != ref.key
    assert (await checkpoints.load_tail(runtime.objects, first.tail, hydrate=True)).items[0].content == original.content
    assert (await checkpoints.load_tail(runtime.objects, grown.tail, hydrate=True)).items[0].content == changed.content


async def test_structured_values_keep_their_type_and_not_a_partial_json_preview(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    lease = Lease("run_body", "rat_body", "thr_body", tenant.organization_id, tenant.workspace_id, 1, "w", "t")
    value = [{"type": "text", "text": "z" * 40000}, {"type": "image", "url": "https://example.test/image"}]
    written = await checkpoints.publish_display(runtime, lease, Snapshot([], Tail(items=[item(value, "result_parts")])))
    preview = await checkpoints.load_tail(runtime.objects, written.tail)
    assert "result_parts" not in preview.items[0].content
    assert preview.items[0].content_refs["result_parts"].media_type == "application/json"
    hydrated = await checkpoints.load_tail(runtime.objects, written.tail, hydrate=True)
    assert hydrated.items[0].content["result_parts"] == value


async def test_changed_json_types_do_not_reuse_an_equal_python_value() -> None:
    writes = []

    async def write(data):  # type: ignore[no-untyped-def]
        writes.append(data)
        return reference(f"body/{len(writes)}", data, "application/zstd")

    contents = Contents()
    first = await contents.item(item({"flag": True, "padding": "x" * 40000}, "value"), write)
    second = await contents.item(item({"flag": 1, "padding": "x" * 40000}, "value"), write)
    assert first.content_refs["value"].id != second.content_refs["value"].id
    assert len(writes) == 2


async def test_full_display_preserves_the_existing_transport_events() -> None:
    sources = [
        PartStartEvent(index=0, part=ToolCallPart("large", None, "call_one")),
        PartDeltaEvent(index=0, delta=ToolCallPartDelta(args_delta="a" * 280000, tool_call_id="call_one")),
        PartEndEvent(index=0, part=ToolCallPart("large", "a" * 280000, "call_one")),
        FunctionToolResultEvent(ToolReturnPart("large", "result" * 60000, "call_one")),
    ]
    bounded = SemanticFold("run_body", attempt=1)
    complete = DisplayFold("run_body", Tail(), attempt=1, page_items=100, page_bytes=10000000)

    class Sink:
        def __init__(self) -> None:
            self.events: list[Any] = []

        def delta(self, event):  # type: ignore[no-untyped-def]
            self.events.append(event)

    sink = Sink()
    expected = []
    async with Coalescer(complete, sink, window=0) as output:  # type: ignore[arg-type]
        for index, source in enumerate(sources):
            event = HarnessEvent(thread_id="thread", run_id="harness", sequence=index, occurred_at=AT, event=source)
            expected.extend(bounded.fold(bounded.events(event), event))
            output.observe(event)
    assert [event.model_dump() for event in sink.events] == [event.model_dump() for event in expected]
    call = next(value for value in complete.items.values() if value.kind == "tool_call")
    assert call.content["arguments"] == "a" * 280000
    assert call.content["result"] == "result" * 60000


async def test_large_argument_continuation_restores_the_complete_prefix(runtime, tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(display_contents, "MAX_CONTENT_BYTES", INLINE_BYTES + 1)
    lease = Lease("run_body", "rat_body", "thr_body", tenant.organization_id, tenant.workspace_id, 1, "w", "t")
    fold = DisplayFold(lease.run_id, Tail(), attempt=1, page_items=1, page_bytes=1024)

    def event(text: str) -> dict:
        return {
            "type": "CUSTOM",
            "name": "a13n.pydantic_ai.part_delta",
            "value": {
                "thread_id": "thread",
                "run_id": "harness",
                "event": {
                    "index": 0,
                    "delta": {"part_delta_kind": "tool_call", "args_delta": text},
                },
            },
        }

    fold.fold([event("x" * 40000)])
    first = await checkpoints.publish_display(runtime, lease, fold.snapshot())
    preview = await checkpoints.load_tail(runtime.objects, first.tail)
    assert preview.continuation_ref is not None
    assert not preview.continuation_ref.truncated
    assert preview.items[0].content_refs["value"].truncated
    assert preview.continuation is not None and preview.continuation.arguments is not None
    assert preview.continuation.arguments.event is None
    restored = await checkpoints.load_tail(runtime.objects, first.tail, hydrate=True)
    resumed = DisplayFold(lease.run_id, restored, attempt=1, page_items=1, page_bytes=1024)
    resumed.fold([event("suffix")])
    value = next(iter(resumed.items.values())).content["value"]
    assert value["event"]["delta"]["args_delta"] == "x" * 40000 + "suffix"


async def test_upload_without_committed_display_is_reclaimed_but_current_attempt_is_kept(
    runtime, tenant, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    run = RunRow(id="run_body", organization_id=tenant.organization_id, workspace_id=tenant.workspace_id)
    lease = Lease(run.id, "rat_body", "thr_body", run.organization_id, run.workspace_id, 1, "w", "t")
    original = runtime.objects.put

    async def fail_tail(key, data, *, content_type):  # type: ignore[no-untyped-def]
        if "/tail/" in key:
            raise OSError("tail publication failed")
        return await original(key, data, content_type=content_type)

    monkeypatch.setattr(runtime.objects, "put", fail_tail)
    with pytest.raises(OSError):
        await checkpoints.publish_display(runtime, lease, Snapshot([], Tail(items=[item("x" * 40000)])))
    prefix = checkpoints.run_prefix(run.organization_id, run.id)
    orphaned = await runtime.objects.keys(prefix, limit=100)
    assert len(orphaned) == 1 and "/display-contents/1/" in orphaned[0]
    current = f"{prefix}/display-contents/2/current"
    await original(current, b"current", content_type="text/plain")
    async with transaction(runtime.storage) as session:
        await checkpoints.reclaim(session, run, before_attempt=2)
    await Delivery(
        runtime.storage,
        {checkpoints.CLEANUP: partial(checkpoints.clean, runtime)},
        owner="clean",
        policies=runtime.settings.outbox.policies,
    )()
    assert await runtime.objects.keys(prefix, limit=100) == [current]


@pytest.mark.parametrize("limited", [False, True])
async def test_history_content_api_authorizes_references_and_page_cleanup_keeps_bodies(
    service, scripted_model, runs_kit, monkeypatch, limited
) -> None:  # type: ignore[no-untyped-def]
    if limited:
        monkeypatch.setattr(display_contents, "MAX_CONTENT_BYTES", 40000)
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    whole = "完整正文🙂" * 5000
    saved = whole.encode()[:40000].decode("utf-8", errors="ignore") if limited else whole
    scripted_model.say(whole)
    started = await runs_kit.start_thread(service, agent, "answer")
    run_id = started["run"]["id"]
    settings = service.runtime.settings
    worker = settings.worker.model_copy(update={"page_items": 1})
    runtime = replace(service.runtime, settings=settings.model_copy(update={"worker": worker}))
    await (await runs_kit.attempt(service, runtime=runtime))
    listing = await runs_kit.items(service, run_id)
    message = next(value for value in listing["items"] if value["content"].get("role") == "assistant")
    ref = message["content_refs"]["text"]
    path = f"{service.api}/runs/{run_id}/contents/{ref['id']}"
    response = await service.client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["value"] == saved
    assert response.json()["truncated"] is limited
    assert ref["truncated"] is limited
    assert response.headers["cache-control"] == "no-store"
    other = await runs_kit.start_thread(service, agent, "another")
    refused = await service.client.get(f"{service.api}/runs/{other['run']['id']}/contents/{ref['id']}")
    assert refused.status_code == 404

    # Completed items move to immutable pages; cleanup must read only the database dependencies.
    async with short_session(service.runtime.storage) as session:
        page = await session.scalar(
            select(RunItemPageRow).where(
                RunItemPageRow.run_id == run_id, RunItemPageRow.refs.contains([{"id": ref["id"]}])
            )
        )
        assert page is not None
    await Delivery(
        service.runtime.storage,
        {checkpoints.CLEANUP: partial(checkpoints.clean, service.runtime)},
        owner="clean",
        policies=service.runtime.settings.outbox.policies,
    )()
    response = await service.client.get(path)
    assert response.status_code == 200 and response.json()["value"] == saved
