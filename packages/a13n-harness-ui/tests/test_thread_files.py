from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.attachments import read_attachment
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.pastes import PendingPastes
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.thread_files import AttachmentUpload, ComposerInput, ThreadFiles
from anyio import fail_after, sleep, to_thread
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, TextContent, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from .test_interactive import _seed


def _expire(path: Path) -> None:
    os.utime(path, (1, 1))


@pytest.mark.anyio
async def test_scratch_is_lazy_reused_and_pruned_without_retained_inputs(tmp_path: Path) -> None:
    files = ThreadFiles(tmp_path, retention_seconds=1)
    assert not files.root.exists()
    first = await files.stage("thread-one", AttachmentUpload("../notes.txt", b"notes"))
    second = await files.stage("thread-one", AttachmentUpload("notes.txt", b"other"))
    assert first.name == second.name == "notes.txt"
    assert first.attachment_id != second.attachment_id
    assert (await files.retain("thread-one", first.attachment_id))[1] == b"notes"
    assert (await files.retain("thread-one", first.attachment_id))[1] == b"notes"
    directory = await files.touch("thread-one")
    (directory / "tmp" / "script.py").write_text("print('hello')")
    assert await files.touch("thread-one") == directory
    _expire(directory / "tmp")
    other = ThreadFiles(tmp_path, retention_seconds=1)
    assert await other.prune() == ()
    await files.close()
    _expire(directory / "tmp")
    assert await other.prune() == ("thread-one",)
    assert not (directory / "tmp").exists()
    assert (await other.read("thread-one", first.attachment_id))[1] == b"notes"
    with pytest.raises(ValueError, match="expired"):
        await other.read("thread-one", second.attachment_id)
    await other.close()


@pytest.mark.anyio
async def test_prune_protects_another_process_and_recovers_after_crash(tmp_path: Path) -> None:
    script = """
import asyncio, sys
from pathlib import Path
from a13n_harness_ui.thread_files import ThreadFiles
async def main():
    files = ThreadFiles(Path(sys.argv[1]))
    await files.touch('thread-active')
    print('ready', flush=True)
    sys.stdin.readline()
asyncio.run(main())
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    assert child.stdout is not None
    files = ThreadFiles(tmp_path, retention_seconds=1)
    try:
        assert await to_thread.run_sync(child.stdout.readline) == "ready\n"
        scratch = files.directory("thread-active") / "tmp"
        _expire(scratch)
        assert await files.prune() == ()
        assert scratch.is_dir()
        child.kill()
        await to_thread.run_sync(child.wait)
        # Windows can finish process wait before releasing its file locks.
        # Pruning is best effort: require eventual recovery, not a first-probe win.
        with fail_after(5):
            while True:
                removed = await files.prune()
                if removed:
                    break
                await sleep(0.05)
        assert removed == ("thread-active",)
        assert not scratch.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
        await files.close()


@pytest.mark.anyio
async def test_thread_files_reject_escape_oversize_and_symlink_reads(tmp_path: Path) -> None:
    files = ThreadFiles(tmp_path)
    with pytest.raises(ValueError, match="identifier"):
        await files.touch("../outside")
    with pytest.raises(ValueError, match="10 MiB"):
        await files.stage("thread-one", AttachmentUpload("big.bin", b"x" * (10 * 1024 * 1024 + 1)))
    attachment = await files.stage("thread-one", AttachmentUpload("small.txt", b"safe"))
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    target = tmp_path / "threads/thread-one/tmp/uploads" / attachment.attachment_id / "content"
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic"):
        await files.read("thread-one", attachment.attachment_id)
    with pytest.raises(ValueError, match="missing"):
        await files.read("thread-two", attachment.attachment_id)
    await files.close()


def test_long_pastes_expand_exactly_once_and_delete_as_blocks() -> None:
    pastes = PendingPastes()
    assert pastes.insert("short\r\ntext") == "short\ntext"
    first = pastes.insert("large\n" * 250)
    second = pastes.insert(first + "x" * 1100)
    assert first != second
    assert pastes.expand("prefix " + first + second) == "prefix " + "large\n" * 250 + first + "x" * 1100
    assert pastes.deletion(first, len(first), backward=True) == len(first)
    assert pastes.deletion(first, 0, backward=False) == len(first)
    pastes.retain((second,))
    assert pastes.expand(first) == first
    assert pastes.expand(second) == first + "x" * 1100


def test_file_acquisition_keeps_original_name_and_validates_images(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    assert read_attachment(path) == AttachmentUpload("notes.txt", b"hello", "text/plain")
    path = tmp_path / "fake.png"
    path.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="valid"):
        read_attachment(path)


@pytest.mark.anyio
async def test_composer_inputs_survive_restart_and_scratch_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness.model_auth as runtime

    path = await _seed(tmp_path, monkeypatch)
    image = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image, format="PNG")
    observed = []

    async def stream_model(messages, info):
        observed.append(messages)
        yield "Received"

    monkeypatch.setattr(
        runtime,
        "CodexRequestModel",
        lambda *args, **kwargs: FunctionModel(stream_function=stream_model, profile={"supports_thinking": True}),
    )
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data", scratch_retention_seconds=1.0))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        await backend.initialize()
        text = "long pasted source\n" * 200
        await backend.execute(
            StreamRenderer(backend.status),
            prompt=ComposerInput(
                text=text,
                attachments=(
                    AttachmentUpload("clipboard.png", image.getvalue()),
                    AttachmentUpload("notes.txt", b"notes"),
                ),
                source_id="input-composer-test",
            ),
        )
        assert backend.thread_id is not None
        thread_id = backend.thread_id
        transcript = await app.get_thread_transcript(thread_id=thread_id)
        serialized = transcript.model_dump_json()
        assert "clipboard.png" in serialized and "notes.txt" in serialized
        assert "input-composer-test" in serialized
        retained = tuple((tmp_path / "data/threads" / thread_id / "attachments").iterdir())
        assert len(retained) == 2
        with pytest.raises(ValueError, match="blank"):
            await app.submit_thread(thread_id=thread_id, prompt=ComposerInput(""))
    scratch = tmp_path / "data/threads" / thread_id / "tmp"
    _expire(scratch)
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert not scratch.exists()  # Automatic startup janitor.
        assert all(item.is_dir() for item in retained)
        for item in retained:
            metadata, data = await app.read_thread_attachment(thread_id=thread_id, attachment_id=item.name)
            assert data == (image.getvalue() if metadata.name == "clipboard.png" else b"notes")
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Recall both inputs")
        await app.wait_root_operation(receipt.receipt_id)
    assert len(observed) == 2
    contents = [
        item
        for message in observed[-1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
        for item in part.content
    ]
    assert any(isinstance(item, BinaryContent) and item.data == image.getvalue() for item in contents)
    assert any(isinstance(item, TextContent) and item.content == text for item in contents)


@pytest.mark.anyio
async def test_staging_rejects_symlinked_upload_directory(tmp_path: Path) -> None:
    files = ThreadFiles(tmp_path)
    directory = await files.touch("thread-one")
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "tmp/uploads").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        await files.stage("thread-one", AttachmentUpload("notes.txt", b"notes"))
    assert tuple(outside.iterdir()) == ()
    await files.close()


@pytest.mark.anyio
async def test_read_and_retain_share_the_same_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading

    files = ThreadFiles(tmp_path)
    other = ThreadFiles(tmp_path)
    attachment = await files.stage("thread-one", AttachmentUpload("notes.txt", b"notes"))
    entered, release = threading.Event(), threading.Event()
    original = files._read

    def slow_read(thread_id, attachment_id):
        entered.set()
        assert release.wait(timeout=5)
        return original(thread_id, attachment_id)

    monkeypatch.setattr(files, "_read", slow_read)
    reading = asyncio.create_task(files.read("thread-one", attachment.attachment_id))
    try:
        assert await to_thread.run_sync(entered.wait, 5)
        retaining = asyncio.create_task(other.retain("thread-one", attachment.attachment_id))
        await asyncio.sleep(0.05)
        assert not retaining.done()
        release.set()
        assert (await reading)[1] == b"notes"
        assert (await retaining)[1] == b"notes"
    finally:
        release.set()
        await reading
        await files.close()
        await other.close()
