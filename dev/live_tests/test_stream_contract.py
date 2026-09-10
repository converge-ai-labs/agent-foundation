"""Hand-authored contract examples and corruptions; never serialize Service models."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from .stream import Event, parse_events
from .stream_contract import AguiSequence, assert_hosted_contract, assert_native_envelope

REQUEST = {"runId": "external-run", "threadId": "external-thread"}
TEXT = [
    {"type": "RUN_STARTED", **REQUEST},
    {"type": "TEXT_MESSAGE_START", "messageId": "message-1", "role": "assistant"},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message-1", "delta": "Hello 世界🙂"},
    {"type": "TEXT_MESSAGE_END", "messageId": "message-1"},
    {"type": "RUN_FINISHED", **REQUEST},
]
NATIVE = {
    "schema_version": "1",
    "event_id": "event-1",
    "event_type": "agui.text_message_content",
    "run_id": "run-1",
    "thread_id": "thread-1",
    "run_attempt_id": "attempt-1",
    "harness_run_id": "harness-1",
    "lifecycle_event_id": None,
    "item_id": "item-1",
    "occurred_at": "2026-09-10T12:00:00Z",
    "payload": TEXT[2],
}


def frames(events):
    # Hosted cursors are opaque. Do not accidentally enforce Native Redis syntax.
    return [Event(f"opaque-{i}", "", event) for i, event in enumerate(events)]


def test_valid_contract_examples_allow_additive_fields():
    value = {**NATIVE, "future": {"safe": True}}
    assert_native_envelope(value)
    sequence = assert_hosted_contract(frames(TEXT), REQUEST, "completed")
    assert sequence.assistant_text() == "Hello 世界🙂"


@pytest.mark.parametrize("field", list(NATIVE))
def test_native_required_fields_cannot_be_omitted(field):
    value = deepcopy(NATIVE)
    del value[field]
    with pytest.raises((KeyError, AssertionError)):
        assert_native_envelope(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "2"),
        ("event_id", ""),
        ("thread_id", 1),
        ("occurred_at", "not-a-timestamp"),
        ("occurred_at", "2026-09-10T12:00:00"),
        ("payload", []),
        ("payload", {"messageId": "m"}),
        ("payload", {"messageId": "m", "delta": 42}),
        ("payload", {"type": "RUN_STARTED", "messageId": "m", "delta": "x"}),
        ("harness_run_id", None),
    ],
)
def test_native_malformed_envelope_or_agui_payload_is_rejected(field, value):
    with pytest.raises((AssertionError, ValueError, ValidationError)):
        assert_native_envelope({**NATIVE, field: value})


@pytest.mark.parametrize(
    "fault",
    [
        "missing_start",
        "duplicate_start",
        "orphan_content",
        "late_content",
        "missing_end",
        "empty_delta",
        "missing_terminal",
        "duplicate_terminal",
        "event_after_terminal",
        "wrong_run",
        "wrong_thread",
        "wrong_outcome",
        "raw",
        "execution_field",
        "private_id",
        "private_reasoning",
        "unknown_custom",
        "unknown_custom_version",
        "duplicate_cursor",
    ],
)
def test_hosted_contract_rejects_corrupted_observations(fault):
    events = deepcopy(TEXT)
    if fault == "missing_start":
        events.pop(0)
    elif fault == "duplicate_start":
        events.insert(1, events[0])
    elif fault == "orphan_content":
        events[2]["messageId"] = "unknown"
    elif fault == "late_content":
        events[2], events[3] = events[3], events[2]
    elif fault == "missing_end":
        events.pop(3)
    elif fault == "empty_delta":
        events[2]["delta"] = ""
    elif fault == "missing_terminal":
        events.pop()
    elif fault == "duplicate_terminal":
        events.append(events[-1])
    elif fault == "event_after_terminal":
        events.append(
            {"type": "CUSTOM", "name": "a13n.service.run_status", "value": {"schema_version": "1", "status": "running"}}
        )
    elif fault in {"wrong_run", "wrong_thread"}:
        events[-1]["runId" if fault == "wrong_run" else "threadId"] = "internal-id"
    elif fault == "wrong_outcome":
        events[-1] = {"type": "RUN_ERROR", "message": "failed"}
    elif fault == "raw":
        events[2]["rawEvent"] = {"provider": "secret"}
    elif fault == "execution_field":
        events[2]["worker_id"] = "unobserved-private-worker"
    elif fault in {"private_id", "private_reasoning"}:
        events[2]["delta"] = "internal-id" if fault == "private_id" else "PRIVATE_PROTOCOL_REASONING"
    elif fault in {"unknown_custom", "unknown_custom_version"}:
        events.insert(
            1,
            {
                "type": "CUSTOM",
                "name": "a13n.harness.debug" if fault == "unknown_custom" else "a13n.service.run_status",
                "value": {"schema_version": "2" if fault == "unknown_custom_version" else "1", "status": "running"},
            },
        )
    stream = frames(events)
    if fault == "duplicate_cursor":
        stream[2] = Event(stream[1].cursor, "", stream[2].data)
    with pytest.raises((AssertionError, ValidationError)):
        assert_hosted_contract(stream, REQUEST, "completed", private_ids=["internal-id"])


TOOLS = [
    {"type": "TOOL_CALL_START", "toolCallId": "call-1", "toolCallName": "search"},
    {"type": "TOOL_CALL_ARGS", "toolCallId": "call-1", "delta": '{"q":'},
    {"type": "TOOL_CALL_ARGS", "toolCallId": "call-1", "delta": '"hello"}'},
    {"type": "TOOL_CALL_END", "toolCallId": "call-1"},
    {"type": "TOOL_CALL_RESULT", "toolCallId": "call-1", "messageId": "result-1", "content": "found", "role": "tool"},
]


def test_interleaved_tools_have_independent_argument_accumulators():
    other = [{**event, "toolCallId": "call-2"} for event in TOOLS]
    sequence = AguiSequence()
    for first, second in zip(TOOLS, other, strict=True):
        sequence.accept(first)
        sequence.accept(second)
    sequence.close()
    assert len(sequence.tools) == 2 and all(tool["result"] for tool in sequence.tools.values())


@pytest.mark.parametrize("reason", ["lease_expired", "retry_after_failure", "planned_handoff", "pending_input"])
def test_recovery_preserves_external_run_without_an_extra_lifecycle(reason):
    recovery = {
        "type": "CUSTOM",
        "name": "a13n.service.run_recovery",
        "value": {
            "schema_version": "1",
            "event_id": "recovery-1",
            "runId": REQUEST["runId"],
            "reason": reason,
        },
    }
    assert_hosted_contract(frames([TEXT[0], recovery, *TEXT[1:]]), REQUEST, "completed")
    assert_native_envelope(
        {
            **NATIVE,
            "event_type": "run.recovery",
            "item_id": None,
            "lifecycle_event_id": "lifecycle-1",
            "payload": {"reason": reason},
        }
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "worker_crash"),
        ("schema_version", "2"),
        ("event_id", ""),
        ("runId", "another-run"),
        ("worker_id", "private-worker"),
    ],
)
def test_hosted_recovery_rejects_invalid_or_private_fields(field, value):
    recovery = {
        "type": "CUSTOM",
        "name": "a13n.service.run_recovery",
        "value": {
            "schema_version": "1",
            "event_id": "recovery-1",
            "runId": REQUEST["runId"],
            "reason": "lease_expired",
            field: value,
        },
    }
    with pytest.raises(AssertionError):
        assert_hosted_contract(frames([TEXT[0], recovery, *TEXT[1:]]), REQUEST, "completed")


@pytest.mark.parametrize("terminal", ["RUN_FINISHED", "RUN_ERROR"])
def test_waiting_cannot_masquerade_as_success_or_error(terminal):
    waiting = {
        "type": "CUSTOM",
        "name": "a13n.service.run_status",
        "value": {
            "schema_version": "1",
            "status": "waiting",
            "pending": {"schema_version": "1", "calls": [], "resolution_policy": "all"},
        },
    }
    ending = {"type": terminal, **REQUEST} if terminal == "RUN_FINISHED" else {"type": terminal, "message": "failed"}
    with pytest.raises(AssertionError):
        assert_hosted_contract(frames([TEXT[0], waiting, ending]), REQUEST, "waiting")


@pytest.mark.parametrize(
    "fault", ["missing_start", "wrong_call", "bad_json", "late_args", "early_result", "duplicate_result", "missing_end"]
)
def test_tool_sequence_rejects_broken_correlations_and_order(fault):
    events = deepcopy(TOOLS)
    if fault == "missing_start":
        events.pop(0)
    elif fault == "wrong_call":
        events[1]["toolCallId"] = "unknown"
    elif fault == "bad_json":
        events[1]["delta"] = "!"
    elif fault == "late_args":
        events.insert(4, events[1])
    elif fault == "early_result":
        events[3], events[4] = events[4], events[3]
    elif fault == "duplicate_result":
        events.append(events[-1])
    elif fault == "missing_end":
        events = events[:3]
    sequence = AguiSequence()
    with pytest.raises((AssertionError, ValueError)):
        for event in events:
            sequence.accept(event)
        sequence.close()


@pytest.mark.anyio
async def test_current_native_replay_gap_has_no_checkpoint():
    async def lines():
        for line in ["event: a13n.service.replay_gap", 'data: {"run_id":"run-1"}', ""]:
            yield line

    with pytest.raises(AssertionError, match="replay gap"):
        _ = [event async for event in parse_events(lines())]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source,match",
    [
        (["id: 12-0", ": heartbeat", ""], "heartbeat"),
        (["id:", ": heartbeat", ""], "heartbeat"),
        (["id: 12-0", "event: run.completed", 'data: {"payload": NaN}', ""], "Non-JSON"),
        (
            ["id: 12-0", "event: run.completed", 'data: {"event_type":', 'data: "run.completed"}', ""],
            "one JSON data line",
        ),
        (["id: 12-0", "event: run.completed", "data: {}"], "middle"),
    ],
)
async def test_native_framing_rejects_nonconforming_server_output(source, match):
    async def lines():
        for line in source:
            yield line

    with pytest.raises(AssertionError, match=match):
        _ = [event async for event in parse_events(lines())]
