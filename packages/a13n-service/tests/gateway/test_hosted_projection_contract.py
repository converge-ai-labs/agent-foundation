"""Spec 22: public identities and message snapshots must agree with delivered AG-UI."""

import json
from datetime import UTC, datetime

import pytest
from a13n_service.gateway.hosted_agui import _messages_from_entries, _project_observation
from a13n_service.run_stream import RunStreamEntry, RunStreamEvent, deterministic_run_stream_event_id


def entry(index, kind, payload):
    return RunStreamEntry(
        f"{index + 1}-0",
        RunStreamEvent(
            event_id=deterministic_run_stream_event_id("public-contract", str(index)),
            event_type=f"agui.{kind}",
            run_id="run_1234567890abcdef",
            thread_id="thread_1234567890abcdef",
            run_attempt_id="rat_1234567890abcdef",
            harness_run_id="hrun_private",
            occurred_at=datetime(2026, 9, 10, tzinfo=UTC),
            payload=payload,
        ),
    )


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("text_message_start", {"role": "user"}),
        ("text_message_content", {"delta": "private execution context"}),
        ("text_message_end", {}),
    ],
)
def test_model_only_input_is_not_hosted_presentation(kind, fields):
    source = entry(0, kind, {"messageId": "hrun_private:input", "metadata": {"display": False}, **fields})
    assert _project_observation(source) is None


def test_message_identity_is_public_stable_and_shared_with_snapshot():
    source_id = "hrun_private:input:0"
    entries = [
        entry(0, "text_message_start", {"messageId": source_id, "role": "user"}),
        entry(1, "text_message_content", {"messageId": source_id, "delta": "hello"}),
        entry(2, "text_message_end", {"messageId": source_id}),
    ]
    events = [_project_observation(item) for item in entries]
    assert "hrun_private" not in json.dumps(events)
    assert len({event["messageId"] for event in events}) == 1
    assert [_project_observation(item) for item in entries] == events
    assert _messages_from_entries(entries) == ({"id": events[0]["messageId"], "role": "user", "content": "hello"},)


@pytest.mark.parametrize("parent", [None, "hrun_private:assistant"])
def test_tool_only_snapshot_uses_the_parent_identity_delivered_to_clients(parent):
    entries = [
        entry(0, "tool_call_start", {"toolCallId": "call-1", "toolCallName": "search", "parentMessageId": parent}),
        entry(1, "tool_call_args", {"toolCallId": "call-1", "delta": '{"q":"hello"}'}),
        entry(2, "tool_call_end", {"toolCallId": "call-1"}),
    ]
    started = _project_observation(entries[0])
    messages = _messages_from_entries(entries)
    assert started["parentMessageId"] and "hrun_private" not in started["parentMessageId"]
    assert len(messages) == 1 and messages[0]["id"] == started["parentMessageId"]
    assert messages[0]["role"] == "assistant"
    assert messages[0]["toolCalls"][0]["id"] == started["toolCallId"]
    assert messages[0]["toolCalls"][0]["function"] == {"name": "search", "arguments": '{"q":"hello"}'}
