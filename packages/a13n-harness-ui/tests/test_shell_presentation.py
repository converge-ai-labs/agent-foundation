"""Shell invocation rows stay ordinary; only new background completions notify."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer


def _visible(renderer: StreamRenderer, *, detailed: bool = False, width: int = 120) -> str:
    renderer.transcript.detailed = detailed
    renderer.transcript.dirty = True
    renderer.transcript.render(width)
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
    assert "pytest -q" in text and "failed · exit 127" in text
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


def test_pending_shell_uses_one_borderless_command_and_status_row() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec")
    assert _visible(renderer) == "Run pytest -q …"
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


@pytest.mark.parametrize("mode", ["concise", "detailed"])
@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("name", ["a13n.harness_ui.checkpoint", "plugin.test.fact"])
def test_internal_and_unknown_capability_events_are_not_conversation_content(mode: str, child: bool, name: str) -> None:
    renderer = StreamRenderer(Status(mode=mode))
    try:
        renderer.ingest(
            "CUSTOM",
            {
                "name": name,
                "value": {
                    "event": {
                        "event_kind": "capability",
                        "continuation_id": "checkpoint-one",
                        "message": "internal fact",
                    }
                },
            },
            child=child,
        )
        assert not renderer.transcript.blocks
        assert _visible(renderer) == ""
        assert _visible(renderer, detailed=True) == ""
        assert renderer.drain() == ""
        assert not renderer.gap
    finally:
        renderer.transcript.close()


def _capture(renderer: StreamRenderer, result: dict, *, run: str = "root") -> None:
    renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "call-one", "content": json.dumps(result)}, run_id=run)


def _wait(renderer: StreamRenderer, *, run: str = "root", process_id: str = "process-one") -> None:
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "call-one", "tool_call_name": "shell_wait"}, run_id=run)
    renderer.ingest(
        "TOOL_CALL_ARGS", {"tool_call_id": "call-one", "delta": json.dumps({"process_id": process_id})}, run_id=run
    )
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "call-one"}, run_id=run)


@pytest.mark.parametrize("streams", [{}, {"stdout": {"text": ""}, "stderr": {"text": ""}}])
@pytest.mark.parametrize(
    ("phase", "code", "label"),
    [("running", None, "running"), ("exited", 0, "exit 0"), ("exited", 2, "failed · exit 2")],
)
def test_redirected_or_uncaptured_output_needs_only_one_row(streams, phase, code, label) -> None:
    renderer = StreamRenderer(Status())
    command = "pytest -q > /tmp/tests.log 2>&1"
    _start(renderer, name="shell_exec", command=command)
    _capture(renderer, {"ok": True, "status": {"phase": phase, "exit_code": code}, **streams})
    assert _visible(renderer) == f"Run {label} · {command}"
    assert '"command":' in _visible(renderer, detailed=True)
    renderer.transcript.close()


def test_merged_stdout_is_literal_output_not_inferred_stderr_or_tool_data() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec", command="check 2>&1")
    output = '  [bold]ERROR[/bold]\n{"status":{"phase":"failed"}}\n--- file.py'
    _capture(
        renderer,
        {
            "ok": True,
            "status": {"phase": "exited", "exit_code": 0},
            "stdout": {"text": output, "coverage": "complete"},
            "stderr": {"text": "", "coverage": "complete"},
        },
    )
    text = _visible(renderer)
    assert text == "Run exit 0 · check 2>&1"
    expanded = _visible(renderer, detailed=True)
    assert "[bold]ERROR[/bold]" in expanded and "--- file.py" in expanded
    assert json.dumps(output) in next(iter(renderer.transcript.blocks.values())).source
    renderer.transcript.close()


def test_short_shell_output_is_only_in_expanded_details() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec", command="printf done")
    _capture(renderer, {"status": {"phase": "exited", "exit_code": 0}, "stdout": {"text": "done\n"}})
    assert _visible(renderer) == "Run exit 0 · printf done"
    assert '"text": "done\\n"' in _visible(renderer, detailed=True)
    renderer.transcript.close()


def test_shell_wait_uses_retained_launch_command_without_overwriting_it() -> None:
    renderer = StreamRenderer(Status())
    command = "pytest -q > tests.log 2>&1"
    _start(renderer, name="shell_exec", command=command)
    _result(renderer)
    _wait(renderer)
    assert _visible(renderer).splitlines()[-1] == f"Run waiting · {command}"
    _capture(renderer, {"process_id": "process-one", "status": {"phase": "exited", "exit_code": 0}})
    assert _visible(renderer).splitlines()[-1] == f"Run exit 0 · {command}"
    assert renderer._shell_processes[("root", "process-one")].command == command
    assert "process-one" not in _visible(renderer)
    renderer.transcript.close()


@pytest.mark.parametrize("unavailable", ["other-run", "other-process", "evicted"])
def test_shell_wait_does_not_guess_missing_or_cross_run_commands(unavailable: str) -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec", command="original")
    _result(renderer)
    if unavailable == "evicted":
        renderer.clear_process_observations()
    run = "other" if unavailable == "other-run" else "root"
    _wait(renderer, run=run, process_id="process-other" if unavailable == "other-process" else "process-one")
    assert _visible(renderer).splitlines()[-1] == "Run waiting · command unavailable"
    _capture(renderer, {"status": {"phase": "exited", "exit_code": 0}}, run=run)
    assert _visible(renderer).splitlines()[-1] == "Run exit 0 · command unavailable"
    renderer.transcript.close()


@pytest.mark.parametrize("width", [28, 80])
@pytest.mark.parametrize("phase", ["timed_out", "cancelled", "failed"])
def test_long_commands_and_empty_output_cannot_hide_explicit_outcome(width: int, phase: str) -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec", command="long-command " * 100)
    _capture(renderer, {"status": {"phase": phase, "exit_code": None}})
    renderer.transcript.render(width)
    text = "\n".join("".join(part[1] for part in row) for row in renderer.transcript.rows)
    assert phase.replace("_", " ") in text
    assert len(text.splitlines()) > 1
    assert "long-command" in text
    renderer.transcript.close()


def test_empty_partial_capture_and_disclosure_are_not_silently_hidden() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec", command="check > tests.log 2>&1")
    _capture(
        renderer,
        {
            "status": {"phase": "exited", "exit_code": 0},
            "stdout": {"text": "", "coverage": "complete", "content_complete": False, "omitted_before_bytes": 12},
            "stderr": {"text": "", "coverage": "unknown"},
            "disclosure": {"truncated": True},
        },
    )
    text = _visible(renderer)
    assert "exit 0" in text and "failed" not in text
    assert text.startswith("Run exit 0 · output unknown · output incomplete · output omitted · output disclosure · ")
    assert len(text.splitlines()) == 1
    expanded = _visible(renderer, detailed=True)
    assert '"omitted_before_bytes": 12' in expanded and '"truncated": true' in expanded
    renderer.transcript.close()


def test_long_capture_cannot_push_coverage_notices_out_of_preview_budget() -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec", command="check")
    _capture(
        renderer,
        {
            "status": {"phase": "exited", "exit_code": 1},
            "stderr": {"text": "x" * 2000, "coverage": "complete"},
            "stdout": {"text": "", "coverage": "partial"},
            "disclosure": {"truncated": True},
        },
    )
    text = _visible(renderer)
    assert text == "Run failed · exit 1 · output partial · output disclosure · check"
    assert "x" * 20 in _visible(renderer, detailed=True)
    renderer.transcript.close()


def test_start_only_shell_is_one_row_before_arguments_arrive() -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": "shell_exec"})
    assert _visible(renderer) == "Run command unavailable …"
    renderer.transcript.close()


@pytest.mark.parametrize("name", ["shell_exec", "shell_wait"])
@pytest.mark.parametrize("width", [28, 80, 120])
@pytest.mark.parametrize(
    "output", ["", "captured line\n", "captured line\n" * 100], ids=["empty", "one-line", "many-lines"]
)
def test_shell_exec_and_wait_wrap_summary_without_dumping_capture(name: str, width: int, output: str) -> None:
    renderer = StreamRenderer(Status())
    if name == "shell_wait":
        _wait(renderer)
    else:
        _start(renderer, name=name, command="check 2>&1")
    _capture(
        renderer,
        {
            "status": {"phase": "running", "exit_code": None},
            "stdout": {"text": output, "coverage": "partial"},
            "stderr": {"text": output, "coverage": "partial"},
        },
    )
    text = _visible(renderer, width=width)
    from prompt_toolkit.utils import get_cwidth

    assert all(get_cwidth(line) <= width for line in text.splitlines())
    assert "captured line" not in text
    assert "output partial" in " ".join(text.split())
    preview = next(iter(renderer.transcript.blocks.values())).preview
    assert preview is not None and preview.count("output partial") == 1
    if output:
        assert "captured line" in _visible(renderer, detailed=True)
    renderer.transcript.close()


@pytest.mark.parametrize(
    ("result", "label"),
    [
        ('{"ok":true}', "status unavailable"),
        ('{"ok":false,"error":{"code":"denied"}}', "failed"),
        ('{"ok":false,"status":{"phase":"unknown"}}', "failed · status unavailable"),
        ("unstructured tool output", "status unavailable"),
    ],
)
def test_unavailable_native_status_never_falls_back_to_output_preview(result: str, label: str) -> None:
    renderer = StreamRenderer(Status())
    _start(renderer, name="shell_exec", command="check")
    renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "call-one", "content": result}, run_id="root")
    assert _visible(renderer) == f"Run {label} · check"
    expanded = _visible(renderer, detailed=True)
    assert '"command": "check"' in expanded
    if result.startswith("{"):
        assert '"ok":' in expanded
    else:
        assert result in expanded
    renderer.transcript.close()


@pytest.mark.parametrize("mode", ["concise", "detailed"])
def test_shell_review_timeout_renders_observed_denial_without_a_frontend_timer(mode) -> None:
    renderer = StreamRenderer(Status(mode=mode))
    try:
        _start(renderer, name="shell_exec")
        assert "Automatically denied" not in _visible(renderer)
        renderer.ingest(
            "CUSTOM",
            {
                "name": "a13n.harness.tool",
                "value": {
                    "event": {
                        "kind": "tool",
                        "payload": {
                            "type": "tool_review_result",
                            "status": "error",
                            "error_code": "tool_review_timeout",
                            "decision": "deny",
                            "result": None,
                            "tool_id": "environment.shell_exec",
                            "tool_name": "shell_exec",
                            "tool_call_id": "call-one",
                        },
                    }
                },
            },
        )
        visible = _visible(renderer, detailed=mode == "detailed")
        assert "Shell review · timed out" in visible
        assert "Automatically denied; tool was not executed" in visible
        assert "call-one" in visible
        assert renderer.background_hint == ""
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("decision", ["deny", "approval_required", "allow"])
@pytest.mark.parametrize("reason", ["Review reason", None])
def test_unified_shell_review_result_uses_existing_custom_event_renderer(decision, reason) -> None:
    renderer = StreamRenderer(Status(mode="concise"))
    try:
        _start(renderer, name="shell_exec")
        renderer.ingest(
            "CUSTOM",
            {
                "name": "a13n.harness.tool",
                "value": {
                    "event": {
                        "kind": "tool",
                        "payload": {
                            "type": "tool_review_result",
                            "status": "completed",
                            "tool_id": "environment.shell_exec",
                            "tool_call_id": "call-one",
                            "decision": decision,
                            "result": {
                                "assessment": {
                                    "risk": "high",
                                    "reason": reason,
                                },
                                "usage": [],
                            },
                        },
                    }
                },
            },
        )
        visible = _visible(renderer)
        assert ("Review reason" in visible) is (decision != "allow" and reason is not None)
        assert "None" not in visible
        if decision != "allow":
            assert "call-one" in visible
            assert "Risk: high" in visible
    finally:
        renderer.transcript.close()


def test_unified_review_timeout_renders_without_local_timer() -> None:
    from a13n_harness_ui.interactive.panels import capability_panel

    panel = capability_panel(
        "a13n.harness.tool",
        {
            "payload": {
                "type": "tool_review_result",
                "status": "error",
                "error_code": "tool_review_timeout",
                "tool_id": "environment.shell_exec",
                "tool_call_id": "call-one",
                "decision": "deny",
                "result": None,
            }
        },
    )
    assert panel is not None and panel.title == "Shell review · timed out"
    assert "Automatically denied" in panel.body and "call-one" in panel.body
