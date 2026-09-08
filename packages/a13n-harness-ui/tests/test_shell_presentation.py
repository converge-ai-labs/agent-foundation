"""Shell invocation rows stay ordinary; only new background completions notify."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer


def _visible(renderer: StreamRenderer, *, detailed: bool = False) -> str:
    renderer.transcript.detailed = detailed
    renderer.transcript.dirty = True
    renderer.transcript.render(120)
    return "\n".join("".join(part[1] for part in row) for row in renderer.transcript.rows)


def _status(renderer, *, phase="exited", code=0, callback=False, run="root", child=False):
    renderer.ingest(
        "CUSTOM",
        {
            "name": "a13n.shell.status",
            "value": {
                "event": {
                    "event_kind": "capability",
                    "process_id": "process-one",
                    "phase": phase,
                    "exit_code": code,
                    "callback": callback,
                }
            },
        },
        run_id=run,
        child=child,
    )


def _start(renderer, *, name="shell_start", command="pytest -q", run="root", child=False):
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "call-one", "tool_call_name": name}, run_id=run, child=child)
    renderer.ingest(
        "TOOL_CALL_ARGS",
        {"tool_call_id": "call-one", "delta": json.dumps({"command": command})},
        run_id=run,
        child=child,
    )
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "call-one"}, run_id=run, child=child)


def _result(renderer, *, phase="running", code=None, run="root", child=False):
    renderer.ingest(
        "TOOL_CALL_RESULT",
        {
            "tool_call_id": "call-one",
            "content": json.dumps(
                {
                    "ok": True,
                    "process_id": "process-one",
                    "status": {"phase": phase, "exit_code": code},
                    "stdout": {"text": "output", "coverage": "complete"},
                }
            ),
        },
        run_id=run,
        child=child,
    )


@pytest.mark.parametrize("mode", ["concise", "detailed"])
def test_routine_shell_status_events_never_create_duplicate_panels(mode: str) -> None:
    renderer = StreamRenderer(Status(mode=mode))
    _start(renderer)
    _status(renderer, phase="running", code=None)
    _status(renderer, code=127)
    _result(renderer, phase="exited", code=127)
    _status(renderer, code=127, callback=True)
    assert len(renderer.transcript.blocks) == 1
    text = _visible(renderer)
    assert "pytest -q" in text and "Result · failed" in text
    assert "process-one" not in text
    assert "Shell ·" not in text and "Exit code" not in text and "Event ·" not in text
    assert '"exit_code": 127' in _visible(renderer, detailed=True)
    renderer.transcript.close()


@pytest.mark.parametrize("child", [False, True])
def test_background_count_and_inspection_track_returned_handles_even_when_hidden(child: bool) -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, child=child)
    _status(renderer, phase="running", child=child)
    assert renderer.background_hint == ""  # In-flight foreground call is not background work.
    _result(renderer, child=child)
    assert renderer.background_hint == "Background 1 observed · /ps"
    details = renderer.process_details()
    assert "process-one · running · pytest -q · Run root" in details
    _status(renderer, child=child, callback=True)
    assert renderer.background_hint == ""
    assert "process-one · exited" in renderer.process_details()
    renderer.transcript.close()


def test_delayed_running_result_does_not_resurrect_completed_background_process() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer)
    _status(renderer, callback=True)
    _result(renderer, phase="running")
    assert renderer.background_hint == ""
    assert "process-one · exited · pytest -q" in renderer.process_details()
    renderer.transcript.close()


def test_process_observation_end_is_run_scoped_and_gaps_are_not_complete_counts() -> None:
    renderer = StreamRenderer(Status())
    for run in ("root", "child"):
        _start(renderer, run=run)
        _result(renderer, run=run)
    assert "Background 2" in renderer.background_hint
    renderer.ingest("RUN_FINISHED", {}, run_id="root")
    assert "Background 1" in renderer.background_hint
    assert "unavailable" in renderer.process_details()
    renderer.gap = True
    assert "1+" in renderer.background_hint
    assert "incomplete" in renderer.process_details()
    renderer.clear_process_observations()
    assert "No background processes observed" in renderer.process_details()
    renderer.transcript.close()


def test_pending_shell_uses_compact_framed_command_and_waiting_state() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer)
    assert len(_visible(renderer).splitlines()) == 3
    assert "shell_start · pytest -q" in _visible(renderer)
    assert "Waiting for output" in _visible(renderer)
    assert "╭" in _visible(renderer) and "╰" in _visible(renderer)
    renderer.transcript.close()


@pytest.mark.parametrize(
    ("phase", "code", "label"),
    [
        ("exited", 0, "finished"),
        ("exited", 127, "failed"),
        ("timed_out", None, "timed out"),
        ("cancelled", None, "cancelled"),
        ("signaled", None, "interrupted"),
    ],
)
def test_background_completion_is_a_single_command_first_notice(phase, code, label) -> None:
    renderer = StreamRenderer(Status())
    _start(renderer)
    _result(renderer)
    _status(renderer, phase=phase, code=code, callback=True)
    _status(renderer, phase=phase, code=code, callback=True)
    assert len(renderer.transcript.blocks) == 2
    text = _visible(renderer)
    assert f"pytest -q · {label}" in text
    assert "process-one" not in text and "exit_code" not in text
    assert "process-one" in _visible(renderer, detailed=True)
    renderer.transcript.close()


def test_completion_correlation_is_run_scoped_and_bounds_retained_state() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, run="one", command="first")
    _result(renderer, run="one", phase="exited", code=0)
    _start(renderer, run="two", command="second")
    _result(renderer, run="two")
    _status(renderer, run="one", callback=True)
    _status(renderer, run="two", callback=True)
    assert "second · finished" in _visible(renderer)
    assert "first · finished" not in _visible(renderer)
    for index in range(200):
        _status(renderer, run=f"later-{index}", callback=True)
    assert len(renderer._shell_processes) == 128
    renderer.transcript.close()


def test_callback_without_retained_command_does_not_invent_identity() -> None:
    renderer = StreamRenderer(Status())
    _status(renderer, callback=True)
    assert _visible(renderer) == "background command · finished"
    renderer.transcript.close()


def test_child_background_completion_obeys_mode_and_keeps_run_identity() -> None:
    renderer = StreamRenderer(Status())
    _status(renderer, callback=True, run="child-run", child=True)
    assert not renderer.transcript.blocks
    renderer.status.mode = "detailed"
    _status(renderer, callback=True, run="child-run", child=True)
    assert "background command · finished · child-run" in _visible(renderer)
    renderer.transcript.close()


def test_unknown_capability_events_still_have_a_fallback() -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest(
        "CUSTOM",
        {
            "name": "plugin.test.fact",
            "value": {
                "event": {
                    "event_kind": "capability",
                    "message": "important fact",
                }
            },
        },
    )
    assert "important fact" in _visible(renderer)
    renderer.transcript.close()
