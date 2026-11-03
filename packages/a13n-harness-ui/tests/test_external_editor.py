from __future__ import annotations

import asyncio
import json
import os
import shlex
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.external_editor import edit_prompt
from a13n_harness_ui.interactive.inline_attachments import InlineAttachments
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.thread_files import AttachmentUpload
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def _editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> Path:
    editor = tmp_path / "editor fixture.py"
    editor.write_text("import sys\nfrom pathlib import Path\npath = Path(sys.argv[-1])\n" + script, encoding="utf-8")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", shlex.join([sys.executable, str(editor), "--wait"]))
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
        with pytest.raises(ValueError, match="Keep each attachment marker"):
            await edit_prompt(text, attachments)
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
Path({str(ready)!r}).write_text(str(path))
time.sleep(60)
""",
    )
    task = asyncio.create_task(edit_prompt("draft", InlineAttachments()))
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        path = Path(ready.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not path.exists()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_background_draft_replacement_is_not_overwritten(monkeypatch) -> None:
    with create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.composer.text = "original"

        async def edit(text, attachments):
            shell.composer.text = "recovered draft"
            return "editor draft"

        monkeypatch.setattr("a13n_harness_ui.interactive.shell.edit_prompt", edit)
        try:
            await shell._edit_prompt()
            assert shell.composer.text == "recovered draft"
            assert "draft changed" in "".join(block.source for block in shell.renderer.transcript.blocks.values())
        finally:
            shell.renderer.transcript.close()
