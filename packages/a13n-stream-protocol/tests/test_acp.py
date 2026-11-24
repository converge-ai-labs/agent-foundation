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
