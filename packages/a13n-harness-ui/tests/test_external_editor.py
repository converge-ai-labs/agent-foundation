from __future__ import annotations

import asyncio
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import psutil
import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.external_editor import EditorDraftError, edit_prompt, editor_command
from a13n_harness_ui.interactive.inline_attachments import InlineAttachments
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.thread_files import AttachmentUpload
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

# Child processes publish complete records before the parent can observe the marker.
_PUBLISH_MARKER = """
def publish_marker(destination, text):
    destination = Path(destination)
    pending = destination.with_name(destination.name + '.tmp')
    pending.write_text(text, encoding='utf-8')
    pending.replace(destination)
"""


@pytest.fixture(autouse=True)
def editor_temp_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> Path:
    editor = tmp_path / "editor fixture.py"
    editor.write_text(
        "import sys\nfrom pathlib import Path\n" + _PUBLISH_MARKER + "\npath = Path(sys.argv[-1])\n" + script,
        encoding="utf-8",
    )
    monkeypatch.delenv("VISUAL", raising=False)
    arguments = [sys.executable, str(editor), "--wait"]
    monkeypatch.setenv("EDITOR", subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments))
    return editor


@pytest.mark.anyio
async def test_editor_roundtrip_prefers_visual_and_uses_private_utf8_file(tmp_path, monkeypatch) -> None:
    record = tmp_path / "observed.json"
    _editor(
        tmp_path,
        monkeypatch,
        f"""
import json, stat
assert sys.argv[1] == '--wait'
Path({str(record)!r}).write_text(json.dumps({{'path': str(path), 'mode': stat.S_IMODE(path.stat().st_mode), 'text': path.read_text(encoding='utf-8')}}))
path.write_bytes('修改后的需求\\r\\n第二段 🌱'.encode('utf-8'))
""",
    )
    monkeypatch.setenv("VISUAL", os.environ["EDITOR"])
    monkeypatch.setenv("EDITOR", "does-not-exist")
    assert await edit_prompt("原始需求\nsecond line", InlineAttachments()) == "修改后的需求\n第二段 🌱"
    observed = json.loads(record.read_text())
    assert observed["text"] == "原始需求\nsecond line"
    if os.name == "posix":
        assert observed["mode"] == stat.S_IRUSR | stat.S_IWUSR
    assert not Path(observed["path"]).exists()


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["keep", "move", "delete", "duplicate", "rename"])
async def test_external_attachment_markers_preserve_identity(tmp_path, monkeypatch, change) -> None:
    observed = tmp_path / "input.txt"
    _editor(
        tmp_path,
        monkeypatch,
        f"""
import re
text = path.read_text(encoding='utf-8')
Path({str(observed)!r}).write_text(text, encoding='utf-8')
markers = re.findall(r'\\[a13n-attachment-[^\\]]+\\]', text)
assert len(markers) == 2
change = {change!r}
if change == 'move':
    text = markers[1] + text.replace(markers[1], '')
elif change == 'delete':
    text = text.replace(markers[0], '')
elif change == 'duplicate':
    text += markers[0]
elif change == 'rename':
    text = text.replace(markers[0], '[changed attachment]')
path.write_text(text + '\\ue123', encoding='utf-8')
""",
    )
    attachments = InlineAttachments()
    first, second = attachments.reserve(), attachments.reserve()
    image, file = (
        AttachmentUpload("image.png", b"image", "image/png"),
        AttachmentUpload("notes.txt", b"notes", "text/plain"),
    )
    attachments.values[first].upload = image
    attachments.values[second].upload = file
    text = f"前 {first} 中 [image#1] 后 {second}"
    if change in {"delete", "duplicate", "rename"}:
        with pytest.raises(EditorDraftError, match="Keep each attachment marker") as caught:
            await edit_prompt(text, attachments)
        recovery = caught.value.recovery_path.read_text(encoding="utf-8")
        assert "前" in recovery and "后" in recovery and "[image#1]" in recovery
        assert recovery.endswith("\ue123")
    else:
        edited = await edit_prompt(text, attachments)
        assert attachments.compile(edited).attachments == ((file, image) if change == "move" else (image, file))
        assert "[image#1]" in edited  # A literal label remains ordinary text.
        assert "a13n-attachment-" not in edited
        assert "\\ue123" in edited  # External private characters cannot acquire identity.
    assert first not in observed.read_text(encoding="utf-8")
    assert second not in observed.read_text(encoding="utf-8")


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["unset", "missing", "exit", "encoding", "removed", "invalid-command"])
async def test_failed_editor_preserves_draft(tmp_path, monkeypatch, failure) -> None:
    scripts = {
        "exit": "path.write_text('discard this')\nsys.exit(2)\n",
        "encoding": "path.write_bytes(b'\\xff')\n",
        "removed": "path.unlink()\n",
    }
    _editor(tmp_path, monkeypatch, scripts.get(failure, ""))
    if failure == "unset":
        monkeypatch.delenv("EDITOR")
    elif failure == "missing":
        monkeypatch.setenv("EDITOR", "a13n-nonexistent-editor")
    elif failure == "invalid-command":
        monkeypatch.setenv("EDITOR", "'unterminated")
    with create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        original = Document("keep my draft", 4)
        shell.composer.buffer.document = original
        try:
            await shell._edit_prompt()
            assert shell.composer.buffer.document == original
            assert "Could not edit prompt" in "".join(
                block.source for block in shell.renderer.transcript.blocks.values()
            )
        finally:
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_ctrl_g_expands_paste_preserves_attachments_and_does_not_send(tmp_path, monkeypatch) -> None:
    seen = tmp_path / "seen.txt"
    _editor(
        tmp_path,
        monkeypatch,
        f"""
text = path.read_text(encoding='utf-8')
Path({str(seen)!r}).write_text(text, encoding='utf-8')
path.write_text(text + '\\n修改完成', encoding='utf-8')
""",
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        shell.job = asyncio.create_task(asyncio.Event().wait())
        assert shell.busy
        shell.handle = AsyncMock()
        shell.cancel = AsyncMock()
        pasted = "长段落\n" * 300
        shell.composer.buffer.document = Document(shell.pastes.insert(pasted), 0)
        shell.insert_attachments((AttachmentUpload("notes.txt", b"content", "text/plain"),))
        before = shell.composer.text
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(5):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
                pipe.send_text("\x07")
                while not shell.composer.text.endswith("修改完成"):
                    await asyncio.sleep(0.01)
            assert pasted in seen.read_text(encoding="utf-8")
            assert "[Pasted text" not in seen.read_text(encoding="utf-8")
            assert len(shell.images) == 1
            shell.handle.assert_not_awaited()
            shell.cancel.assert_not_awaited()
            shell.composer.buffer.undo()
            assert shell.composer.text == before
            assert len(shell.images) == 1
        finally:
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_editor_cancellation_reaps_process_and_removes_file(tmp_path, monkeypatch) -> None:
    ready = tmp_path / "ready"
    _editor(
        tmp_path,
        monkeypatch,
        f"""
import time
publish_marker({str(ready)!r}, str(path))
time.sleep(60)
""",
    )
    task = asyncio.create_task(edit_prompt("draft", InlineAttachments()))
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        path = Path(ready.read_text(encoding="utf-8"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not path.exists()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_background_draft_replacement_is_not_overwritten(tmp_path, monkeypatch) -> None:
    with create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.composer.text = "original"
        shell._recoverable = Document("earlier unsubmitted input")

        async def edit(text, attachments):
            shell.composer.text = "recovered draft"
            return "editor draft"

        monkeypatch.setattr("a13n_harness_ui.interactive.shell.edit_prompt", edit)
        try:
            await shell._edit_prompt()
            assert shell.composer.text == "recovered draft"
            assert shell._recoverable == Document("earlier unsubmitted input")
            (recovery,) = tmp_path.glob("a13n-prompt-recovery-*/prompt.md")
            assert recovery.read_text(encoding="utf-8") == "editor draft"
            assert "draft changed" in "".join(block.source for block in shell.renderer.transcript.blocks.values())
        finally:
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_bom_and_unchanged_folded_draft(tmp_path, monkeypatch) -> None:
    _editor(tmp_path, monkeypatch, "path.write_bytes(b'\\xef\\xbb\\xbf' + path.read_bytes())\n")
    assert await edit_prompt("unchanged\n中文", InlineAttachments()) == "unchanged\n中文"
    with create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.composer.buffer.document = Document(shell.pastes.insert("中文\n" * 400), 0)
        original = shell.composer.buffer.document
        try:
            await shell._edit_prompt()
            assert shell.composer.buffer.document == original
        finally:
            shell.renderer.transcript.close()


async def _until_file(path: Path) -> str:
    async with asyncio.timeout(8):
        while not path.exists():
            await asyncio.sleep(0.01)
    return path.read_text(encoding="utf-8")


def _is_live(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.mark.anyio
async def test_cancel_launcher_stops_its_editor_child(tmp_path, monkeypatch) -> None:
    ready = tmp_path / "child-ready"
    child = f"""
import os, time
from pathlib import Path
{_PUBLISH_MARKER}
publish_marker({str(ready)!r}, str(os.getpid()))
time.sleep(60)
"""
    _editor(tmp_path, monkeypatch, f"import subprocess\nsubprocess.Popen([sys.executable, '-c', {child!r}]).wait()\n")
    task = asyncio.create_task(edit_prompt("draft", InlineAttachments()))
    pid = None
    try:
        pid = int(await _until_file(ready))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(5):
            while _is_live(pid):
                await asyncio.sleep(0.01)
        assert not list(tmp_path.glob("a13n-prompt-*/prompt.md"))
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if pid is not None and _is_live(pid):
            psutil.Process(pid).kill()


@pytest.mark.anyio
@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGTERM grace period; Windows taskkill is immediate")
async def test_repeated_cancellation_waits_for_term_ignoring_editor(tmp_path, monkeypatch) -> None:
    ready, terminated = tmp_path / "ready", tmp_path / "terminated"
    _editor(
        tmp_path,
        monkeypatch,
        f"""
import os, signal, time
signal.signal(signal.SIGTERM, lambda *_: publish_marker({str(terminated)!r}, 'TERM'))
publish_marker({str(ready)!r}, str(os.getpid()) + '\\n' + str(path))
time.sleep(60)
""",
    )
    task = asyncio.create_task(edit_prompt("draft", InlineAttachments()))
    pid = None
    try:
        value = (await _until_file(ready)).splitlines()
        pid, prompt = int(value[0]), Path(value[1])
        task.cancel()
        await _until_file(terminated)
        assert prompt.exists()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not _is_live(pid)
        assert not prompt.exists()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if pid is not None and _is_live(pid):
            psutil.Process(pid).kill()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows CommandLineToArgvW")
@pytest.mark.parametrize(
    "setting,expected",
    [
        (r"C:\Users\O'Brien\editor.exe --wait", [r"C:\Users\O'Brien\editor.exe", "--wait"]),
        ('"C:\\Program Files\\Editor\\editor.exe" --wait', [r"C:\Program Files\Editor\editor.exe", "--wait"]),
        (r'editor.exe "argument with spaces" "a\\\"b"', ["editor.exe", "argument with spaces", 'a\\"b']),
    ],
)
def test_windows_editor_command_uses_native_quote_rules(setting, expected) -> None:
    assert editor_command(setting) == expected


@pytest.mark.anyio
@pytest.mark.skipif(os.name != "nt", reason="Native Windows PATHEXT and .cmd launch")
async def test_windows_cmd_editor_is_resolved_through_pathext(tmp_path, monkeypatch) -> None:
    editor = _editor(tmp_path, monkeypatch, "path.write_text('edited through cmd', encoding='utf-8')\n")
    launcher = tmp_path / "a13n-editor-fixture.cmd"
    launcher.write_text(f'@echo off\n"{sys.executable}" "{editor}" %*\n')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("EDITOR", "a13n-editor-fixture --wait")
    assert await edit_prompt("draft", InlineAttachments()) == "edited through cmd"


@pytest.mark.anyio
async def test_repeated_cancellation_during_process_acquisition(tmp_path, monkeypatch) -> None:
    ready = tmp_path / "editor-ready"
    _editor(
        tmp_path,
        monkeypatch,
        f"import os, time\npublish_marker({str(ready)!r}, str(os.getpid()) + '\\n' + str(path))\ntime.sleep(60)\n",
    )
    release = asyncio.Event()
    spawn = asyncio.create_subprocess_exec

    async def delayed_spawn(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        if args[0] == sys.executable:
            await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(edit_prompt("draft", InlineAttachments()))
    pid = None
    try:
        value = (await _until_file(ready)).splitlines()
        pid, prompt = int(value[0]), Path(value[1])
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        assert _is_live(pid) and prompt.exists()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        assert not _is_live(pid)
        assert not prompt.exists()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if pid is not None and _is_live(pid):
            psutil.Process(pid).kill()


@pytest.mark.anyio
@pytest.mark.parametrize("shutdown", ["cancel", "exit", "error"])
@pytest.mark.parametrize("handed_off", [False, True])
async def test_shell_repeated_cancellation_waits_for_editor_cleanup(
    tmp_path, monkeypatch, terminal_app, shutdown, handed_off
) -> None:
    from a13n_harness_ui.interactive import external_editor
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status

    ready = tmp_path / "editor-ready"
    _editor(
        tmp_path,
        monkeypatch,
        f"import os, time\npublish_marker({str(ready)!r}, str(os.getpid()) + '\\n' + str(path))\ntime.sleep(60)\n",
    )
    stopping, release = asyncio.Event(), asyncio.Event()
    stop_editor = external_editor._stop_editor

    async def delayed_stop(process):
        stopping.set()
        await release.wait()
        await stop_editor(process)

    monkeypatch.setattr(external_editor, "_stop_editor", delayed_stop)
    backend = SessionBackend(terminal_app, CliRequest(), tmp_path, Status())
    backend.interaction = AsyncMock(return_value=None)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), directory=tmp_path)
        shell.composer.text = "keep this draft"
        terminal = asyncio.create_task(shell.app.run_async(set_exception_handler=False)) if handed_off else None
        session = asyncio.create_task(shell.run(backend, terminal_task=terminal))
        pid = None
        try:
            async with asyncio.timeout(5):
                while not shell.ready or not shell.app.is_running:
                    await asyncio.sleep(0.01)
            pipe.send_text("\x07")
            value = (await _until_file(ready)).splitlines()
            pid, prompt = int(value[0]), Path(value[1])
            if shutdown == "cancel":
                session.cancel()
            elif shutdown == "exit":
                shell.app.exit()
            else:
                shell.app.exit(exception=RuntimeError("fixture terminal failure"))
            await asyncio.wait_for(stopping.wait(), timeout=5)
            # Repeated requests must not interrupt the terminal handoff or the
            # background-task drain. Cleanup remains deliberately held.
            for _ in range(3):
                session.cancel()
                await asyncio.sleep(0)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(session), timeout=0.1)
            assert _is_live(pid) and prompt.exists()
            release.set()
            with pytest.raises(RuntimeError if shutdown == "error" else asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(session), timeout=5)
            assert not _is_live(pid)
            assert not prompt.exists()
            assert shell.composer.text == "keep this draft"
        finally:
            release.set()
            if not session.done():
                session.cancel()
            await asyncio.wait_for(asyncio.gather(session, return_exceptions=True), timeout=5)
            if shell._editor_task is not None:
                await asyncio.wait_for(asyncio.gather(shell._editor_task, return_exceptions=True), timeout=5)
            if pid is not None and _is_live(pid):
                psutil.Process(pid).kill()
            shell.renderer.transcript.close()


@pytest.mark.anyio
@pytest.mark.parametrize("shutdown", ["exit", "cancel", "error"])
async def test_application_shutdown_cancels_editor_before_waiting_for_terminal(tmp_path, monkeypatch, shutdown) -> None:
    ready = tmp_path / "editor-ready"
    _editor(
        tmp_path,
        monkeypatch,
        f"""
import os, time
publish_marker({str(ready)!r}, str(os.getpid()) + '\\n' + str(path))
time.sleep(60)
""",
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        shell.composer.text = "keep this draft"
        application = asyncio.create_task(shell.app.run_async())
        pid = None
        try:
            async with asyncio.timeout(5):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
            pipe.send_text("\x07")
            value = (await _until_file(ready)).splitlines()
            pid, prompt = int(value[0]), Path(value[1])
            assert _is_live(pid) and prompt.exists()
            if shutdown == "cancel":
                application.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(application), timeout=5)
            elif shutdown == "error":
                shell.app.exit(exception=RuntimeError("fixture terminal failure"))
                with pytest.raises(RuntimeError, match="fixture terminal failure"):
                    await asyncio.wait_for(asyncio.shield(application), timeout=5)
            else:
                shell.app.exit()
                await asyncio.wait_for(asyncio.shield(application), timeout=5)
            assert not _is_live(pid)
            assert not prompt.exists()
            assert shell.composer.text == "keep this draft"
        finally:
            if pid is not None and _is_live(pid):
                psutil.Process(pid).kill()
            if not application.done():
                application.cancel()
            await asyncio.gather(application, return_exceptions=True)
            shell.renderer.transcript.close()
