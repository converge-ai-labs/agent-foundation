from itertools import product

import pytest
from a13n_harness import (
    Envelope,
    ExecutionRef,
    HarnessState,
    InputOrigin,
    Sender,
    disposition,
    envelope_ids,
    input_source,
    render_envelope,
)
from a13n_harness.capabilities.steering import STEERING_CAPABILITY_ID, _SteeringState
from a13n_harness.content import (
    ContentItem,
    ContentMetadata,
    input_request,
    merge_request_history,
    request_input_content,
)
from a13n_harness.events import enqueued_input_events, input_events
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState, encode_messages
from pydantic import ValidationError
from pydantic_ai.messages import EnqueuedMessagesEvent


@pytest.mark.parametrize(
    "condition,delivery",
    list(product(("active", "preparing", "idle", "waiting", "paused", "archived"), ("steer", "next_run"))),
)
def test_disposition(condition, delivery):
    expected = (
        "reject"
        if condition == "archived"
        else "start"
        if condition == "idle"
        else "steer"
        if (condition, delivery) == ("active", "steer")
        else "hold"
    )
    assert disposition(condition, delivery) == expected


@pytest.mark.parametrize("kind,label", [("agent", "Subagent result"), ("tool", "Tool notification")])
def test_result_rendering_and_roundtrip(kind, label):
    envelope = Envelope(
        id="inbox-1",
        kind="result",
        sender=Sender(kind="system"),
        execution=ExecutionRef(execution_id="execution-1", kind=kind, name="reviewer", state="succeeded"),
        text="Final reply",
    )
    (item,) = render_envelope(envelope)
    assert item.value.startswith(f"{label} · reviewer · execution_id=execution-1 · succeeded\n\nFinal reply")
    assert ("send_message" if kind == "agent" else "execution_result") in item.value
    origin = input_source(request_input_content(input_request((item,)))[0].metadata)
    assert origin.id == envelope.id and origin.execution == envelope.execution
    assert "delivery" not in item.metadata.model_extra["a13n.input"]
    assert "text" not in item.metadata.model_extra["a13n.input"]
    with pytest.raises(ValidationError):
        envelope.text = "changed"


def test_messages_events_and_cancellation():
    sender = Sender(kind="agent", thread_id="thread_source", name="Release notes")
    assert (
        render_envelope(Envelope(id="m", kind="agent_message", sender=sender, text="Question"))[0].value
        == 'Message · from thread "Release notes" (thread_source)\n\nQuestion'
    )
    execution = ExecutionRef(execution_id="child-1", kind="agent", name="reviewer", state="running")
    child = render_envelope(
        Envelope(id="c", kind="agent_message", sender=sender, execution=execution, text="Question")
    )[0]
    assert child.value.startswith("Subagent message · reviewer · execution_id=child-1 · running")
    assert (
        render_envelope(Envelope(id="e", kind="event", sender=sender, text="Existing header\nBody"))[0].value
        == "Existing header\nBody"
    )
    cancelled = render_envelope(
        Envelope(
            id="x",
            kind="result",
            sender=sender,
            execution=execution.model_copy(update={"state": "cancelled"}),
            text="Must not appear",
        )
    )[0]
    assert "Must not appear" not in cancelled.value


@pytest.mark.parametrize(
    "source,kind,sender",
    [
        ("async_subagent", "result", "agent"),
        ("tool_execution", "result", "system"),
        ("parent_agent", "agent_message", "agent"),
        ("background_process", "event", "system"),
        ("envd_session", "event", "system"),
    ],
)
def test_legacy_markers(source, kind, sender):
    origin = input_source({"a13n.steering-source": source})
    assert (origin.kind, origin.sender.kind, origin.id, origin.execution) == (kind, sender, None, None)
    assert input_source(ContentMetadata.model_validate({"a13n.steering-source": source})) == origin


def test_nested_thread_marker_and_unmarked_content():
    origin = input_source(
        {"harness_ui": {"thread_message": {"source_thread_id": "thread_source", "source_thread_title": "Source"}}}
    )
    assert origin == InputOrigin(
        kind="agent_message", sender=Sender(kind="agent", thread_id="thread_source", name="Source")
    )
    assert input_source({"source_id": "a13n-harness-ui.thread-lifecycle"}).kind == "event"
    assert input_source({"source_id": "arbitrary"}) is None
    assert input_source(None) is None


@pytest.mark.parametrize("marker", [None, "bad", [], {}, {"kind": "invented", "sender": {"kind": "user"}}])
def test_malformed_marker_never_becomes_user_input(marker):
    metadata = ContentMetadata.model_validate({"a13n.input": marker})
    assert input_source(metadata) == InputOrigin(kind="event", sender=Sender(kind="system", name="unknown"))
    assert input_events((ContentItem("notice", metadata),), source="user", input_id="i")[0].source != "user"


def test_forward_compatible_reader_but_strict_construction():
    marker = {
        "id": "i",
        "kind": "result",
        "sender": {"kind": "agent", "future": 1},
        "execution": {"execution_id": "e", "kind": "agent", "name": "worker", "state": "succeeded", "future": 1},
        "future": 1,
    }
    assert input_source({"a13n.input": marker, "a13n.steering-source": "parent_agent"}).kind == "result"
    with pytest.raises(ValidationError):
        InputOrigin.model_validate(marker)
    assert input_source({"a13n.input": None, "a13n.steering-source": "parent_agent"}).kind == "event"


def test_envelope_ids_cover_history_and_pending_replay_without_losing_merged_items():
    def request(id):
        return input_request(
            render_envelope(Envelope(id=id, kind="event", sender=Sender(kind="system"), text="same text"))
        )

    merged = merge_request_history((request("a"), request("b")))
    state = HarnessState.new().model_copy(
        update={
            "message_history_json": encode_messages(merged),
            "agent_context_state": AgentContextStateSnapshot(
                entries={
                    STEERING_CAPABILITY_ID: CapabilityState(
                        version="1",
                        data=_SteeringState(pending_requests=(request("b"), request("c"))).model_dump(mode="json"),
                    )
                }
            ),
        }
    )
    assert envelope_ids(HarnessState.model_validate_json(state.model_dump_json())) == ("a", "b", "c")
    assert envelope_ids(HarnessState.new()) == ()
    observed = enqueued_input_events(EnqueuedMessagesEvent(enqueue_id="q", messages=merged))
    assert len(observed) == 2 and all(e.source != "user" for e in observed)
