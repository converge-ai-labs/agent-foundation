"""Consumer assertions derived from spec/a13n-service/{21,22,24} and AG-UI.

AG-UI field schemas come from the pinned upstream package, not Service models.
Ordering follows https://docs.ag-ui.com/concepts/events. These assertions do not
import the Service serializer, observer, projector, or event registry.
"""

from __future__ import annotations

import json
from datetime import datetime

from ag_ui.core import Event as AguiEvent
from pydantic import TypeAdapter

AGUI = TypeAdapter(AguiEvent)
RECOVERY_REASONS = {"lease_expired", "retry_after_failure", "planned_handoff", "pending_input"}
HOSTED_CUSTOM = {
    "a13n.service.run_status",
    "a13n.service.run_recovery",
    "a13n.service.artifact",
    "a13n.service.replay_gap",
}
DEFAULT_HOSTED_TYPES = {
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "CUSTOM",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "TOOL_CALL_START",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "TOOL_CALL_RESULT",
}


def nonempty(value):
    assert isinstance(value, str) and value, "Expected a nonempty string"


def assert_native_envelope(value: dict) -> None:
    """Spec 24, Run Redis Stream: required v1 fields; additive fields are allowed."""
    assert value["schema_version"] == "1", "Unknown required RunStreamEvent schema version"
    for field in ("event_id", "event_type", "run_id", "thread_id"):
        nonempty(value[field])
    for field in ("run_attempt_id", "harness_run_id", "lifecycle_event_id", "item_id"):
        if value[field] is not None:
            nonempty(value[field])
    nonempty(value["occurred_at"])
    assert datetime.fromisoformat(value["occurred_at"]).tzinfo is not None
    assert isinstance(value["payload"], dict), "RunStreamEvent payload must be an object"
    if value["event_type"] == "run.recovery":
        assert value["payload"]["reason"] in RECOVERY_REASONS
        assert value["run_attempt_id"] and value["lifecycle_event_id"]
        assert value["item_id"] is None
    if value["event_type"].startswith("agui."):
        assert value["run_attempt_id"] and value["harness_run_id"]
        AGUI.validate_python(agui_payload(value), strict=True)


def agui_payload(value: dict) -> dict:
    kind = value["event_type"].removeprefix("agui.").upper()
    payload = value["payload"]
    assert payload.get("type", kind) == kind, "Conflicting AG-UI discriminator"
    return {**payload, "type": kind}


class AguiSequence:
    """Text and argument state are independent, allowing interleaved tool calls."""

    def __init__(self, *, prior_tools=()):
        self.messages = {}
        self.open_messages = set()
        self.tools = {call_id: {"args": "", "ended": True, "result": False} for call_id in prior_tools}

    def accept(self, event):
        AGUI.validate_python(event, strict=True)
        kind = event["type"]
        if kind.startswith(("TEXT_MESSAGE_", "REASONING_MESSAGE_")):
            family, phase = kind.rsplit("_", 1)
            assert phase != "CHUNK", "The selected Protocol profile uses explicit start/content/end"
            nonempty(event["messageId"])
            key = (family, event["messageId"])
            if phase == "START":
                assert key not in self.messages, "Duplicate message start"
                self.messages[key] = {"role": event.get("role"), "text": ""}
                self.open_messages.add(key)
            else:
                assert key in self.open_messages, "Message content/end without an open start"
                if phase == "CONTENT":
                    nonempty(event["delta"])
                    self.messages[key]["text"] += event["delta"]
                elif phase == "END":
                    self.open_messages.remove(key)
        elif kind.startswith("TOOL_CALL_"):
            call_id = event["toolCallId"]
            nonempty(call_id)
            if kind == "TOOL_CALL_START":
                assert call_id not in self.tools, "Duplicate tool start"
                nonempty(event["toolCallName"])
                self.tools[call_id] = {"args": "", "ended": False, "result": False}
            else:
                assert call_id in self.tools, "Tool event without a matching call"
                tool = self.tools[call_id]
                if kind == "TOOL_CALL_ARGS":
                    assert not tool["ended"], "Arguments after TOOL_CALL_END"
                    tool["args"] += event["delta"]
                elif kind == "TOOL_CALL_END":
                    assert not tool["ended"], "Duplicate tool end"
                    assert isinstance(json.loads(tool["args"]), dict), "Tool arguments must form a JSON object"
                    tool["ended"] = True
                elif kind == "TOOL_CALL_RESULT":
                    assert tool["ended"] and not tool["result"], "Early or duplicate tool result"
                    tool["result"] = True

    def close(self):
        assert not self.open_messages, "Successful stream left an open message"
        assert all(tool["ended"] for tool in self.tools.values()), "Unterminated tool arguments"

    def assistant_text(self):
        return "".join(value["text"] for value in self.messages.values() if value["role"] == "assistant")


def assert_native_contract(events, run, *, prior_tools=()):
    from .stream import assert_stream

    outcome = run["status"]
    assert_stream(events, run["id"], outcome)
    sequences = {}
    item_identities = {}
    active_attempt = None
    for index, event in enumerate(events):
        value = event.data
        assert_native_envelope(value)
        assert value["run_id"] == run["id"] and value["thread_id"] == run["thread_id"]
        if event.kind == "run_attempt.leased":
            active_attempt = value["run_attempt_id"]
        if event.kind == "run.recovery":
            assert index > 0 and events[index - 1].kind == "run_attempt.leased"
            leased = events[index - 1].data
            for field in ("run_attempt_id", "lifecycle_event_id", "occurred_at"):
                assert value[field] == leased[field], f"Recovery changed leased source {field}"
        if event.kind.startswith("agui."):
            assert value["run_attempt_id"] == active_attempt, "Observation from a superseded or unactivated Attempt"
            assert event.kind != "agui.run_started", "The observer has no authoritative Run-start source"
            sequence = sequences.setdefault(value["harness_run_id"], AguiSequence(prior_tools=prior_tools))
            payload = agui_payload(value)
            sequence.accept(payload)
            if payload["type"].startswith(("TEXT_MESSAGE_", "TOOL_CALL_")):
                nonempty(value["item_id"])
                key = (value["harness_run_id"], payload.get("toolCallId") or payload.get("messageId"))
                assert item_identities.setdefault(key, value["item_id"]) == value["item_id"], "Item identity changed"
    if outcome == "completed":
        # Recovery can leave interrupted partial observations in an earlier Attempt.
        if sequences:
            list(sequences.values())[-1].close()
    return sequences


def assert_hosted_contract(frames, request, outcome, *, prior_tools=(), private_ids=()):
    """Spec 22 default visibility, external identity, and durable lifecycle profile."""
    assert frames, "Hosted AG-UI produced no events"
    assert all(frame.cursor for frame in frames), "Hosted retained events need replay cursors"
    assert len({frame.cursor for frame in frames}) == len(frames), "Duplicate hosted cursor"
    bodies = [frame.data for frame in frames]
    assert bodies[0]["type"] == "RUN_STARTED"
    assert sum(event["type"] == "RUN_STARTED" for event in bodies) == 1
    sequence = AguiSequence(prior_tools=prior_tools)
    terminals, waiting = [], []
    for index, event in enumerate(bodies):
        sequence.accept(event)
        assert event["type"] in DEFAULT_HOSTED_TYPES, "Event outside default Hosted visibility"
        assert not event.get("rawEvent"), "Hosted event exposed raw source data"
        assert not {
            "run_attempt_id",
            "harness_run_id",
            "worker_id",
            "lease_token",
            "fence",
            "stream_id",
        }.intersection(event), "Hosted event exposed execution envelope fields"
        for key in ("runId", "threadId"):
            if key in event:
                assert event[key] == request[key], f"Hosted event changed external {key}"
        if event["type"] in {"RUN_FINISHED", "RUN_ERROR"}:
            terminals.append((index, event["type"]))
        if event["type"] == "CUSTOM":
            assert event["name"] in HOSTED_CUSTOM, "Unregistered Hosted custom event"
            value = event["value"]
            assert isinstance(value, dict) and value["schema_version"] == "1"
            if "runId" in value:
                assert value["runId"] == request["runId"]
            if "run_id" in value:
                assert value["run_id"] == request["runId"]
            if event["name"] == "a13n.service.run_status" and value["status"] == "waiting":
                waiting.append(value)
            if event["name"] == "a13n.service.run_recovery":
                assert set(value) == {"schema_version", "event_id", "runId", "reason"}
                nonempty(value["event_id"])
                assert value["reason"] in RECOVERY_REASONS
            assert event["name"] != "a13n.service.replay_gap", "Hosted delivery history is incomplete"
        encoded = json.dumps(event)
        for number, identifier in enumerate((*private_ids, "PRIVATE_PROTOCOL_REASONING")):
            fields = [key for key, value in event.items() if identifier in json.dumps(value)]
            assert identifier not in encoded, (
                f"Hosted {event['type']} exposed private execution data #{number} in {fields}"
            )
    if outcome == "waiting":
        assert not terminals and len(waiting) == 1, "Waiting must close without success/error"
        assert bodies[-1].get("name") == "a13n.service.run_status"
    else:
        assert not waiting
        expected = "RUN_FINISHED" if outcome == "completed" else "RUN_ERROR"
        assert terminals == [(len(bodies) - 1, expected)], "Expected one final durable outcome"
    if outcome in {"completed", "waiting"}:
        sequence.close()
    return sequence
