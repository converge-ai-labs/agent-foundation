from __future__ import annotations

import pytest
from a13n_stream_protocol.display import (
    BlockAppend,
    BlockPut,
    BlocksRemove,
    DisplayBlock,
    DisplayDelta,
    DisplayGap,
    DisplayPosition,
    DisplayScope,
    DisplaySnapshot,
    DisplayState,
    Producer,
    ScopePut,
)
from pydantic import ValidationError


def _state() -> DisplayState:
    return DisplayState(DisplaySnapshot(position=DisplayPosition(producer=Producer(run_id="run-a", generation="1"))))


def _scope() -> ScopePut:
    return ScopePut(scope=DisplayScope(id="root", thread_id="thread-a", run_id="run-a"))


def _block() -> BlockPut:
    return BlockPut(
        expected_revision=0,
        block=DisplayBlock(id="text", scope_id="root", kind="text", revision=1, content={"text": "Hello"}),
    )


def test_batch_application_is_atomic_and_captures_are_detached() -> None:
    state = _state()
    baseline = state.capture()
    first = state.publish([_scope(), _block()])
    assert first is not None
    frozen = state.capture()
    append = BlockAppend(id="text", field="text", value=" world", expected_revision=1, revision=2)
    with pytest.raises(DisplayGap, match="revision"):
        state.publish([append, append])
    assert state.capture() == frozen
    assert state.publish([append]) is not None
    assert state.capture().blocks[0].content["text"] == "Hello world"
    assert frozen.blocks[0].content["text"] == "Hello"
    frozen.blocks[0].content["text"] = "tampered"
    assert state.capture().blocks[0].content["text"] == "Hello world"
    assert baseline.blocks == ()
    assert not state.apply(first)


def test_uncovered_gap_and_producer_change_require_baseline() -> None:
    state = _state()
    with pytest.raises(DisplayGap, match="contiguous"):
        state.apply(
            DisplayDelta(producer=state.position.producer, from_sequence=1, through_sequence=2, operations=(_scope(),))
        )
    with pytest.raises(DisplayGap, match="producer"):
        state.apply(
            DisplayDelta(
                producer=Producer(run_id="run-a", generation="2"),
                from_sequence=0,
                through_sequence=1,
                operations=(_scope(),),
            )
        )
    assert state.position.sequence == 0
    assert state.publish([]) is None
    assert state.position.sequence == 0


def test_retention_is_an_explicit_atomic_operation() -> None:
    state = _state()
    state.publish([_scope(), _block()])
    state.publish([BlocksRemove(ids=("text",), omitted=1)])
    snapshot = state.capture()
    assert snapshot.blocks == ()
    assert snapshot.omitted == 1
    with pytest.raises(DisplayGap, match="absent"):
        state.publish([BlocksRemove(ids=("missing",), omitted=2)])
    assert state.capture() == snapshot


def test_wire_roundtrip_and_dense_sequence_validation() -> None:
    state = _state()
    delta = state.publish([_scope(), _block()])
    assert delta is not None
    receiver = _state()
    assert receiver.apply(DisplayDelta.model_validate_json(delta.model_dump_json()))
    assert DisplaySnapshot.model_validate_json(state.capture().model_dump_json()) == receiver.capture()
    with pytest.raises(ValidationError, match="exactly one sequence"):
        DisplayDelta(producer=delta.producer, from_sequence=0, through_sequence=2, operations=delta.operations)


def test_scope_identity_and_block_identity_cannot_be_reassigned() -> None:
    state = _state()
    state.publish([_scope(), _block()])
    snapshot = state.capture()
    with pytest.raises(DisplayGap, match="identity"):
        state.publish([ScopePut(scope=_scope().scope.model_copy(update={"run_id": "other"}))])
    with pytest.raises(DisplayGap, match="identity"):
        state.publish(
            [
                BlockPut(
                    expected_revision=1, block=_block().block.model_copy(update={"kind": "reasoning", "revision": 2})
                )
            ]
        )
    assert snapshot == state.capture()


def test_inline_scope_parent_must_precede_child_and_status_is_separate() -> None:
    state = _state()
    child = DisplayScope(
        id="child", thread_id="thread-child", run_id="run-child", parent_scope_id="root", parent_tool_call_id="call-1"
    )
    with pytest.raises(DisplayGap, match="parent"):
        state.publish([ScopePut(scope=child)])
    state.publish([_scope(), ScopePut(scope=child)])
    state.publish([ScopePut(scope=child.model_copy(update={"status": "completed"}))])
    assert state.capture().scopes[1].status == "completed"
    assert state.capture().scopes[0].status == "running"


def test_shared_browser_fixtures_match_python_application() -> None:
    import json
    from pathlib import Path

    fixtures = json.loads(Path(__file__).with_name("display-fixtures.json").read_text())
    state = DisplayState(DisplaySnapshot.model_validate(fixtures["baseline"]))
    for entry in fixtures["accepted"]:
        delta = DisplayDelta.model_validate(entry["delta"])
        assert state.apply(delta), entry["name"]
        assert state.capture().model_dump(mode="json") == entry["snapshot"], entry["name"]
        assert not state.apply(delta)
    for entry in fixtures["invalid"]:
        before = state.capture()
        with pytest.raises(DisplayGap):
            state.apply(DisplayDelta.model_validate(entry["delta"]))
        assert state.capture() == before, entry["name"]
