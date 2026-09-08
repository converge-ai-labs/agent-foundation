"""Workspace-relative tool labels never rewrite execution payloads or source text."""

from __future__ import annotations

import json
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.panels import capability_panel, display_path, tool_preview
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.shell import CliShell
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


@pytest.mark.parametrize(
    "directory, path, expected",
    [
        (PurePosixPath("/work/project"), "/work/project/src/file.py", "src/file.py"),
        (PurePosixPath("/work/project"), "/work/project", "."),
        (PurePosixPath("/work/project"), "/work/project-other/file.py", "/work/project-other/file.py"),
        (PurePosixPath("/work/project"), "/work/other/file.py", "/work/other/file.py"),
        (PurePosixPath("/work/project"), "/work/project/../other/file.py", "/work/project/../other/file.py"),
        (PurePosixPath("/work/project"), "./src/file.py", "./src/file.py"),
        (PurePosixPath("/work/project"), "../file.py", "../file.py"),
        (PurePosixPath("/work/project"), "workspace:/file.py", "workspace:/file.py"),
        (PurePosixPath("/work/project"), "https://host/work/project/file.py", "https://host/work/project/file.py"),
        (PurePosixPath("relative"), "/work/project/file.py", "/work/project/file.py"),
        (None, "/work/project/file.py", "/work/project/file.py"),
        (PureWindowsPath("C:/work/project"), "C:/work/project/src/file.py", r"src\file.py"),
        (PureWindowsPath("C:/work/project"), "C:/work/project", "."),
        (PureWindowsPath("C:/work/project"), "C:/work/project-other/file.py", "C:/work/project-other/file.py"),
        (PureWindowsPath("C:/work/project"), "D:/work/project/file.py", "D:/work/project/file.py"),
        (PureWindowsPath("C:/work/project"), "C:file.py", "C:file.py"),
        (PureWindowsPath("//host/share/project"), "//host/share/project/file.py", "file.py"),
        (PureWindowsPath("//host/share/project"), "//other/share/project/file.py", "//other/share/project/file.py"),
    ],
)
def test_display_path_uses_components_without_resolving(directory: PurePath | None, path: str, expected: str) -> None:
    assert display_path(path, directory) == expected


@pytest.mark.parametrize("name, key", [("view", "file_path"), ("write", "file_path"), ("ls", "path")])
def test_running_and_completed_tool_labels_keep_raw_payloads(name: str, key: str, tmp_path: Path) -> None:
    path = str(tmp_path / "src" / "file.py")
    arguments = {key: path, "content": f"literal source {path}"}
    result = {"ok": True, key: path, "content": f"literal output {path}"}
    renderer = StreamRenderer(Status(directory=tmp_path))
    try:
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": name})
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "one", "delta": json.dumps(arguments)})
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"})
        block = next(iter(renderer.transcript.blocks.values()))
        assert block.preview == f"{dict(view='Read', write='Call write', ls='List')[name]} {Path('src/file.py')} …"
        assert json.dumps(arguments, ensure_ascii=False, indent=2) in block.source
        renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "one", "content": json.dumps(result)})
        assert str(tmp_path) not in (block.preview or "")
        assert str(Path("src/file.py")) in (block.preview or "")
        assert json.dumps(arguments, ensure_ascii=False, indent=2) in block.source
        assert json.dumps(result, ensure_ascii=False, indent=2) in block.source
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("name", ["edit", "multi_edit"])
def test_edit_argument_summary_is_relative(name: str, tmp_path: Path) -> None:
    assert (
        tool_preview(json.dumps({"file_path": str(tmp_path / "file.py")}), name=name, directory=tmp_path) == "file.py"
    )


@pytest.mark.parametrize("oversized", [False, True])
def test_edit_event_shortens_only_title(oversized: bool, tmp_path: Path) -> None:
    path = str(tmp_path / "file.py")
    before = f"old {path}\n" * (2001 if oversized else 1)
    event = {"file_path": path, "before": before, "after": f"new {path}\n"}
    original = event.copy()
    panel = capability_panel("a13n.filesystem.edit_applied", event, directory=tmp_path)
    raw_panel = capability_panel("a13n.filesystem.edit_applied", event)
    assert panel is not None and raw_panel is not None
    assert panel.title.startswith("Edit · file.py · ")
    assert panel.body == raw_panel.body
    assert path in panel.body
    assert event == original


def test_interleaved_child_paths_keep_invocation_base_and_run_identity(tmp_path: Path) -> None:
    renderer = StreamRenderer(Status(directory=tmp_path, mode="detailed"))
    paths = {"root": tmp_path / "file.py", "child": tmp_path.parent / "other" / "file.py"}
    try:
        for run, path in paths.items():
            for kind, payload in (
                ("START", {"tool_call_name": "view"}),
                ("ARGS", {"delta": json.dumps({"file_path": str(path)})}),
                ("END", {}),
            ):
                renderer.ingest(
                    f"TOOL_CALL_{kind}", {"tool_call_id": "same", **payload}, run_id=run, child=run == "child"
                )
        for run in reversed(paths):
            renderer.ingest(
                "TOOL_CALL_RESULT", {"tool_call_id": "same", "content": "done"}, run_id=run, child=run == "child"
            )
        root, child = renderer.transcript.blocks.values()
        assert root.preview == "Read file.py"
        assert "done" not in (root.preview or "") and "done" in root.source
        assert str(tmp_path) not in (root.preview or "")
        assert child.preview == f"Read {paths['child']} · child"
        assert "done" not in (child.preview or "") and "done" in child.source
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize(
    "name, arguments, expected",
    [
        ("shell_exec", {"command": "cat /work/project/file.py"}, "cat /work/project/file.py"),
        ("grep", {"pattern": "/work/project/file.py"}, "/work/project/file.py"),
        ("glob", {"pattern": "/work/project/*.py"}, "/work/project/*.py"),
        ("task_create", {"subject": "/work/project/file.py"}, "/work/project/file.py"),
        ("unknown", {"file_path": "/work/project/file.py"}, "/work/project/file.py"),
    ],
)
def test_non_path_summaries_are_not_rewritten(name: str, arguments: dict[str, str], expected: str) -> None:
    assert tool_preview(json.dumps(arguments), name=name, directory=PurePosixPath("/work/project")) == expected


def test_malformed_arguments_are_not_rewritten() -> None:
    arguments = '{"file_path": "/work/project/file.py'
    assert tool_preview(arguments, name="view", directory=PurePosixPath("/work/project")) == arguments


def test_shell_passes_explicit_directory_and_rendering_does_not_follow_chdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path, status=Status())
        try:
            monkeypatch.chdir(elsewhere)
            assert shell.status.directory == tmp_path
            shell.renderer.ingest(
                "CUSTOM",
                {
                    "name": "a13n.filesystem.edit_applied",
                    "value": {"event": {"file_path": str(tmp_path / "file.py"), "before": "old\n", "after": "new\n"}},
                },
            )
            block = next(iter(shell.renderer.transcript.blocks.values()))
            assert block.source.startswith("Edit · file.py · +1 -1\n")
        finally:
            shell.renderer.transcript.close()


@pytest.mark.parametrize("width", [28, 80])
@pytest.mark.parametrize("name, key", [("view", "file_path"), ("write", "file_path"), ("ls", "path")])
def test_long_tool_paths_are_ellipsized_instead_of_wrapping_out_of_view(width, name, key, tmp_path) -> None:
    from prompt_toolkit.utils import get_cwidth

    relative = Path("packages") / ("long-directory-" * 8) / "file.py"
    path = str(tmp_path / relative)
    renderer = StreamRenderer(Status(directory=tmp_path))
    try:
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": name})
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "one", "delta": json.dumps({key: path})})
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"})
        for completed in (False, True):
            if completed:
                renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "one", "content": '{"ok":true}'})
            renderer.transcript.render(width)
            rows = ["".join(text for _, text in row) for row in renderer.transcript.rows]
            assert len(rows) == 1
            assert "pack" in rows[0] and rows[0].endswith("…")
            assert get_cwidth(rows[0]) <= width
            block = next(iter(renderer.transcript.blocks.values()))
            assert str(relative) in (block.preview or "")
            assert json.dumps({key: path}, ensure_ascii=False, indent=2) in block.source
    finally:
        renderer.transcript.close()
