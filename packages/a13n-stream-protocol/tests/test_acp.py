from a13n_stream_protocol.acp import AcpRunStream
from a13n_stream_protocol.display import DisplayFold
from acp import schema


def test_acp_projection_owns_tool_cards_and_ignores_hidden_echoes():
    events = []
    stream = AcpRunStream("thread-test", "run-test", events.append)
    stream.update(schema.UserMessageChunk(content=schema.TextContentBlock(text="hidden")))
    stream.update(schema.AgentThoughtChunk(content=schema.TextContentBlock(text="reasoning")))
    stream.update(schema.ToolCallStart(tool_call_id="call-1", title="Patch", name="edit", raw_input={"file": "a"}))
    stream.update(
        schema.ToolCallProgress(
            tool_call_id="call-1",
            status="failed",
            locations=[schema.ToolCallLocation(path="/workspace/a", line=3)],
            content=[schema.FileEditToolCallContent(path="/workspace/a", old_text="a", new_text="b")],
            raw_output="failed",
        )
    )
    stream.update(schema.ToolCallProgress(tool_call_id="call-1", status="failed"))
    stream.update(schema.UsageUpdate(used=20, size=100))
    stream.update(
        schema.AgentPlanUpdate(entries=[schema.PlanEntry(content="edit", priority="high", status="completed")])
    )
    stream.update(schema.AgentMessageChunk(content=schema.TextContentBlock(text="answer")))
    stream.finish()
    fold = DisplayFold("run-test")
    fold.fold(events)
    items = fold.export().items
    (tool,) = [item for item in items if item.kind == "tool_call"]
    assert tool.state == "failed"
    assert tool.content["acp"]["locations"] == [{"path": "/workspace/a", "line": 3}]
    assert tool.content["acp"]["content"][0]["newText"] == "b"
    assert [event["type"] for event in events].count("TOOL_CALL_START") == 1
    assert [event["type"] for event in events].count("TOOL_CALL_RESULT") == 1
    assert {item.content["name"] for item in items if item.kind == "observation" and "name" in item.content} == {
        "a13n.acp.plan",
        "a13n.acp.usage",
    }
    assert [item.content["text"] for item in items if item.kind == "text_message"] == ["answer"]
    assert stream.final_text == "answer"


def test_acp_thought_is_closed_and_timed_at_tool_start_or_turn_settlement(monkeypatch):
    clock = iter((10.0, 10.1, 10.2, 12.1, 12.2, 12.3, 12.4, 12.5, 13.0, 13.1, 13.2, 13.3))
    monkeypatch.setattr("a13n_stream_protocol.acp.time", type("Clock", (), {"time": staticmethod(lambda: next(clock))}))
    events = []
    stream = AcpRunStream("thread-test", "run-test", events.append)
    stream.update(schema.AgentThoughtChunk(content=schema.TextContentBlock(text="before a tool")))
    stream.update(schema.ToolCallStart(tool_call_id="call-1", title="Read"))
    stream.update(schema.AgentThoughtChunk(content=schema.TextContentBlock(text="before cancellation")))
    stream.finish()
    reasoning = [event for event in events if event["type"].startswith("REASONING_")]
    assert [event["delta"] for event in reasoning if event["type"].endswith("CONTENT")] == [
        "before a tool",
        "before cancellation",
    ]
    assert reasoning[2]["timestamp"] - reasoning[0]["timestamp"] == 2000
    assert reasoning[5]["timestamp"] > reasoning[3]["timestamp"]
    assert events.index(reasoning[2]) < next(i for i, event in enumerate(events) if event["type"] == "TOOL_CALL_START")


def test_acp_compaction_aggregates_chunks_replaces_summary_bounds_size_and_upserts_in_display():
    events = []
    stream = AcpRunStream("thread-test", "run-test", events.append)
    stream.update(schema.AgentMessageChunk(content=schema.TextContentBlock(text="before")))
    # Chunk arriving before any CompactionUpdate defaults to in_progress and closes active text.
    stream.update(
        schema.SessionUpdateCompactionSummaryChunk(
            compaction_id="cmp-1",
            content=schema.TextContentBlock(text="First "),
        )
    )
    stream.update(
        schema.SessionUpdateCompactionSummaryChunk(
            compaction_id="cmp-1",
            content=schema.TextContentBlock(text="second."),
        )
    )
    stream.update(schema.SessionUpdateCompactionUpdate(compaction_id="cmp-1", status="completed"))
    # Complete replacement summary overwrites accumulated chunks.
    large_summary = "A" * (70 * 1024)
    stream.update(
        schema.SessionUpdateCompactionUpdate(
            compaction_id="cmp-1",
            status="completed",
            summary=[schema.TextContentBlock(text=large_summary)],
        )
    )
    stream.update(
        schema.SessionUpdateCompactionUpdate(
            compaction_id="cmp-2",
            status="failed",
            error="rate_limited",
        )
    )
    stream.update(schema.SessionUpdateCompactionUpdate(compaction_id="cmp-3", status="cancelled"))
    stream.update(schema.AgentMessageChunk(content=schema.TextContentBlock(text="after")))
    stream.finish()

    compaction_events = [
        event["value"] for event in events if event["type"] == "CUSTOM" and event.get("name") == "a13n.acp.compaction"
    ]
    assert compaction_events[0] == {"compactionId": "cmp-1", "status": "in_progress", "summary": "First "}
    assert compaction_events[1] == {"compactionId": "cmp-1", "status": "in_progress", "summary": "First second."}
    assert compaction_events[2] == {"compactionId": "cmp-1", "status": "completed", "summary": "First second."}
    assert compaction_events[3]["status"] == "completed"
    assert len(compaction_events[3]["summary"].encode("utf-8")) == 64 * 1024
    assert compaction_events[4] == {"compactionId": "cmp-2", "status": "failed", "error": "rate_limited"}
    assert compaction_events[5] == {"compactionId": "cmp-3", "status": "cancelled"}
    # Active assistant message was closed before compaction began and never included compaction text.
    assert stream.final_text == "beforeafter"
    assert stream.last_text == "after"
    end_idx = next(i for i, event in enumerate(events) if event["type"] == "TEXT_MESSAGE_END")
    first_cmp_idx = next(i for i, event in enumerate(events) if event.get("name") == "a13n.acp.compaction")
    assert end_idx < first_cmp_idx

    # DisplayFold upserts by per-compaction identity and retains >32 KiB summary under full_content=True.
    full_fold = DisplayFold("run-test", full_content=True)
    full_fold.fold(events)
    compaction_items = [
        item
        for item in full_fold.export().items
        if item.kind == "observation" and item.content.get("name") == "a13n.acp.compaction"
    ]
    assert len(compaction_items) == 3
    by_id = {item.content["value"]["compactionId"]: item.content["value"] for item in compaction_items}
    assert by_id["cmp-1"]["status"] == "completed"
    assert len(by_id["cmp-1"]["summary"].encode("utf-8")) == 64 * 1024
    assert by_id["cmp-2"] == {"compactionId": "cmp-2", "status": "failed", "error": "rate_limited"}
    assert by_id["cmp-3"] == {"compactionId": "cmp-3", "status": "cancelled"}

    compact_fold = DisplayFold("run-test", full_content=False)
    compact_fold.fold(events)
    compact_items = [
        item
        for item in compact_fold.export().items
        if item.kind == "observation" and item.content.get("name") == "a13n.acp.compaction"
    ]
    assert len(compact_items) == 3
    assert any(item.content["value"] == {"omitted": True} for item in compact_items)


def test_acp_compaction_completed_clears_usage_slot_in_display_fold():
    events_cleared = []
    stream_cleared = AcpRunStream("thread-test", "run-cleared", events_cleared.append)
    stream_cleared.update(schema.UsageUpdate(used=900, size=1000))
    stream_cleared.update(schema.SessionUpdateCompactionUpdate(compaction_id="c1", status="in_progress"))
    stream_cleared.update(schema.SessionUpdateCompactionUpdate(compaction_id="c1", status="failed", error="err"))
    stream_cleared.update(schema.SessionUpdateCompactionUpdate(compaction_id="c2", status="cancelled"))
    usage_before_complete = [
        event["value"]
        for event in events_cleared
        if event["type"] == "CUSTOM" and event.get("name") == "a13n.acp.usage"
    ]
    assert usage_before_complete == [{"used": 900, "size": 1000}]

    stream_cleared.update(schema.SessionUpdateCompactionUpdate(compaction_id="c3", status="completed"))
    stream_cleared.finish()
    usage_after_complete = [
        event["value"]
        for event in events_cleared
        if event["type"] == "CUSTOM" and event.get("name") == "a13n.acp.usage"
    ]
    assert usage_after_complete == [{"used": 900, "size": 1000}, {"cleared": True}]

    fold_cleared = DisplayFold("run-cleared", full_content=True)
    fold_cleared.fold(events_cleared)
    (usage_item_cleared,) = [
        item
        for item in fold_cleared.export().items
        if item.kind == "observation" and item.content.get("name") == "a13n.acp.usage"
    ]
    assert usage_item_cleared.content["value"] == {"cleared": True}

    events_refreshed = []
    stream_refreshed = AcpRunStream("thread-test", "run-refreshed", events_refreshed.append)
    stream_refreshed.update(schema.UsageUpdate(used=900, size=1000))
    stream_refreshed.update(schema.SessionUpdateCompactionUpdate(compaction_id="c1", status="completed"))
    stream_refreshed.update(schema.UsageUpdate(used=150, size=1000))
    stream_refreshed.finish()

    fold_refreshed = DisplayFold("run-refreshed", full_content=True)
    fold_refreshed.fold(events_refreshed)
    exported = fold_refreshed.export().items
    (usage_item_refreshed,) = [
        item for item in exported if item.kind == "observation" and item.content.get("name") == "a13n.acp.usage"
    ]
    (compaction_item,) = [
        item for item in exported if item.kind == "observation" and item.content.get("name") == "a13n.acp.compaction"
    ]
    assert usage_item_refreshed.ordinal < compaction_item.ordinal
    assert usage_item_refreshed.content["value"] == {"used": 150, "size": 1000}


def test_acp_prompt_boundary_closes_unfinished_items_and_keeps_run_open():
    events = []
    stream = AcpRunStream("thread-test", "run-test", events.append)
    stream.update(schema.AgentMessageChunk(content=schema.TextContentBlock(text="partial")))
    stream.update(schema.ToolCallStart(tool_call_id="call-1", title="Waiting"))
    stream.update(schema.AgentThoughtChunk(content=schema.TextContentBlock(text="unfinished thought")))
    assert stream.last_text == "partial"

    stream.reset_reply()

    assert [event["type"] for event in events[-3:]] == ["REASONING_MESSAGE_END", "TOOL_CALL_END", "TOOL_CALL_RESULT"]
    assert not any(event["type"] in {"RUN_FINISHED", "RUN_ERROR"} for event in events)
    assert stream.last_text == ""
    stream.update(schema.AgentMessageChunk(content=schema.TextContentBlock(text="follow-up")))
    stream.finish()
    assert stream.last_text == "follow-up"
    assert sum(event["type"] == "TOOL_CALL_END" for event in events) == 1
    assert events[-1]["type"] == "RUN_FINISHED"
