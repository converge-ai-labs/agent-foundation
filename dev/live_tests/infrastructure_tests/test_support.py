"""Offline guards against false-positive live-test observations."""

import json

import pytest

from ..infrastructure.config import local_origin
from ..protocol.stream import Event, assert_stream, parse_events, parse_frames


async def lines(values):
    for value in values:
        yield value


@pytest.mark.anyio
async def test_sse_comments_multiline_and_truncated_frame():
    frame = [
        ": heartbeat",
        "",
        "id: 17-2",
        "event: run.completed",
        'data: {"event_type": "run.completed",',
        'data: "event_id": "evt_1", "run_id": "run_1"}',
        "",
    ]
    events = [event async for event in parse_frames(lines(frame))]
    assert_stream(events, "run_1")
    with pytest.raises(AssertionError, match="middle"):
        _ = [event async for event in parse_frames(lines(frame[:-1]))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind,cursor,match",
    [
        pytest.param("a13n.service.replay_gap", "1-0", "replay gap", id="gap-with-cursor"),
        pytest.param("a13n.service.replay_gap", None, "replay gap", id="gap-without-cursor"),
        pytest.param("run.completed", "bad", "Invalid stream cursor", id="invalid-cursor"),
    ],
)
async def test_sse_rejects_unusable_replay(kind, cursor, match):
    data = {"run_id": "run-1"} if cursor is None else {"event_type": kind}
    frame = ([] if cursor is None else [f"id: {cursor}"]) + [f"event: {kind}", "data: " + json.dumps(data), ""]
    with pytest.raises(AssertionError, match=match):
        _ = [event async for event in parse_events(lines(frame))]


@pytest.mark.parametrize("fault", ["duplicate_identity", "duplicate_cursor", "wrong_run", "wrong_terminal"])
def test_stream_assertions_reject_corrupted_observations(fault):
    events = [
        Event("1-0", "run.running", {"event_id": "evt_1", "run_id": "run_1"}),
        Event("2-0", "run.completed", {"event_id": "evt_2", "run_id": "run_1"}),
    ]
    if fault == "duplicate_identity":
        events[1].data["event_id"] = "evt_1"
    elif fault == "duplicate_cursor":
        events[1] = Event("1-0", events[1].kind, events[1].data)
    elif fault == "wrong_run":
        events[1].data["run_id"] = "run_other"
    else:
        events[1] = Event("2-0", "run.failed", events[1].data)
    with pytest.raises(AssertionError):
        assert_stream(events, "run_1")


@pytest.mark.parametrize(
    "origin",
    ["https://example.com", "http://127.0.0.1@evil.test", "http://127.0.0.1/path", "http://127.0.0.1?token=x"],
)
def test_live_config_rejects_remote_or_credential_origins(origin):
    with pytest.raises(ValueError, match="loopback"):
        local_origin(origin)
