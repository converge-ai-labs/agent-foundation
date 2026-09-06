"""Presentation invariants independent of terminal dimensions or provider access."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

import pytest
from a13n_ui.cli import CliRequest
from a13n_ui.interactive.attachments import MAX_IMAGE_BYTES, add_images, image_bytes, read_image
from a13n_ui.interactive.rendering import Status, StreamRenderer
from a13n_ui.interactive.shell import CliShell
from a13n_ui.interactive.theme import resolve_theme
from a13n_ui.interactive.transcript import Transcript, TranscriptControl
from PIL import Image
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def _png() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (3, 2), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_semantic_markdown_reflows_and_reuses_completed_cache() -> None:
    transcript = Transcript()
    first = transcript.append("# Heading\n\n```python\nx = 123\n```\n[link](https://example.com)", markdown=True)
    transcript.render(80)
    cache = transcript.blocks[first].rows
    assert "\x1b" not in "".join(fragment[1] for row in transcript.rows for fragment in row)
    assert any("bold" in fragment[0] for row in transcript.rows for fragment in row)
    transcript.append("Another block")
    transcript.render(80)
    assert transcript.blocks[first].rows is cache
    transcript.render(12)
    assert transcript.blocks[first].rows is not cache
    cache = transcript.blocks[first].rows
    transcript.theme = resolve_theme("light", environ={})
    transcript.dirty = True
    transcript.render(12)
    assert transcript.blocks[first].rows is not cache


def test_huge_delta_and_cache_budgets_are_visible_and_bounded() -> None:
    transcript = Transcript(max_bytes=8192, max_blocks=8, block_bytes=4096, max_rows=40)
    transcript.append("a" * 100_000, markdown=True)
    assert "Display truncated" in next(iter(transcript.blocks.values())).source
    for index in range(30):
        transcript.append(f"block {index}\n" + "words " * 100)
        transcript.render(15)
    assert transcript.source_bytes <= 8192
    assert len(transcript.blocks) <= 8
    assert len(transcript.rows) <= 41
    assert sum(block.cache_bytes for block in transcript.blocks.values()) <= 8192
    assert transcript.evicted


def test_interleaved_runs_keep_distinct_markdown_blocks_and_scroll_anchor() -> None:
    renderer = StreamRenderer(Status(mode="detailed"))
    for run, text in (("root", "root-1"), ("child", "child-1"), ("root", "root-2")):
        renderer.ingest("TEXT_MESSAGE_CONTENT", {"message_id": "same", "delta": text}, run_id=run, child=run == "child")
    sources = [block.source for block in renderer.transcript.blocks.values()]
    assert sources == ["root-1root-2", "**Subagent**\n\nchild-1"]
    control = TranscriptControl(renderer.transcript)
    control.create_content(80, 4)
    control.scroll(-3)
    anchor = control.position
    renderer.append("new output")
    control.create_content(40, 4)
    assert not control.follow and control.position == anchor
    control.latest()
    control.create_content(40, 4)
    assert control.cursor_row == len(renderer.transcript.rows) - 1


def test_image_validation_does_not_infer_media_type_from_filename(tmp_path: Path) -> None:
    path = tmp_path / "renamed.jpg"
    path.write_bytes(_png())
    image = read_image(path)
    assert image.media_type == "image/png"
    with pytest.raises(ValueError, match="10 MiB"):
        image_bytes("big.png", b"x" * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(ValueError, match="valid"):
        image_bytes("fake.png", b"not an image")
    with pytest.raises(ValueError, match="eight"):
        add_images((image,) * 8, (image,))


@pytest.mark.anyio
async def test_late_clipboard_result_never_enters_replacement_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_ui.interactive.shell as module

    entered, release = threading.Event(), threading.Event()
    image = image_bytes("clipboard.png", _png())

    def clipboard():
        entered.set()
        release.wait(5)
        return (image,)

    monkeypatch.setattr(module, "clipboard_images", clipboard)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        task = asyncio.create_task(shell.acquire_images())
        await asyncio.to_thread(entered.wait, 3)
        shell._draft_generation += 1
        shell.composer.buffer.document = Document("new draft")
        release.set()
        await task
        assert shell.images == ()
        assert shell.composer.text == "new draft"
        assert any("discarded" in block.source for block in shell.renderer.transcript.blocks.values())


@pytest.mark.anyio
async def test_failed_admission_preserves_images_and_next_draft_is_not_overwritten() -> None:
    started, fail = asyncio.Event(), asyncio.Event()

    class Backend:
        async def execute(self, *args, **kwargs):
            started.set()
            await fail.wait()
            raise ValueError("fixture admission failure")

        async def interaction(self):
            return None

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = Backend()
        shell.ready = True
        image = image_bytes("one.png", _png())
        shell.images = (image,)
        shell.send_prompt("inspect")
        await started.wait()
        shell.composer.buffer.document = Document("next draft")
        fail.set()
        await shell.job
        assert shell.composer.text == "next draft"
        assert shell._recoverable == (Document("inspect", 7), (image,))
        await shell.command(shell.registry.parse("/recover"))
        assert shell.composer.text == "inspect"
        assert shell.images == (image,)


@pytest.mark.anyio
async def test_menu_escape_and_multiline_paste_preserve_draft_and_images() -> None:
    initialized = asyncio.Event()

    class Backend:
        async def initialize(self):
            initialized.set()
            return True

        async def interaction(self):
            return None

        async def choices(self, kind):
            from a13n_ui.interactive.selection import Choice

            return (Choice("model-one", "One"), Choice("model-two", "Two"))

    @asynccontextmanager
    async def factory(*args):
        yield Backend()

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest(), runtime_loader=lambda: factory)
        task = asyncio.create_task(shell.run())
        try:
            await initialized.wait()
            shell.composer.buffer.document = Document("unsent", 2)
            shell.images = (image_bytes("draft.png", _png()),)
            await shell.command(shell.registry.parse("/model"))
            assert shell.images == ()
            pipe.send_text("\x1b")
            async with asyncio.timeout(3):
                while shell.menu_handler is not None:
                    await asyncio.sleep(0.01)
            assert shell.composer.buffer.document == Document("unsent", 2)
            assert len(shell.images) == 1
            pipe.send_text("\x03\x1b[200~a\nb\x1b[201~")
            await asyncio.sleep(0.1)
            assert shell.composer.text == "a\nb"
            assert not shell.busy
            pipe.send_text("\x03/quit\r")
            await asyncio.wait_for(task, 3)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_cancel_during_menu_action_restores_saved_text_and_images() -> None:
    from a13n_ui.interactive.selection import Choice

    entered = asyncio.Event()

    async def action(value):
        entered.set()
        await asyncio.Event().wait()

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        original = Document("original draft", 3)
        image = image_bytes("original.png", _png())
        shell.composer.buffer.document = original
        shell.images = (image,)
        shell.open_menu("Import", (Choice("yes", "Import"),), action)
        shell._input_task = asyncio.create_task(shell.menu_answer("1"))
        await entered.wait()
        await shell.cancel()
        assert shell.composer.buffer.document == original
        assert shell.images == (image,)
        assert shell._saved_draft is None and shell.menu_handler is None


@pytest.mark.anyio
async def test_failed_send_recovery_fences_next_drafts_pending_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_ui.interactive.shell as module

    started, fail = asyncio.Event(), asyncio.Event()
    entered, release = threading.Event(), threading.Event()
    original = image_bytes("original.png", _png())
    late = image_bytes("next-draft.png", _png())

    class Backend:
        async def execute(self, *args, **kwargs):
            started.set()
            await fail.wait()
            raise ValueError("admission failed")

        async def interaction(self):
            return None

    def clipboard():
        entered.set()
        release.wait(5)
        return (late,)

    monkeypatch.setattr(module, "clipboard_images", clipboard)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = Backend()
        shell.images = (original,)
        shell.send_prompt("original prompt")
        await started.wait()
        paste = asyncio.create_task(shell.acquire_images())
        await asyncio.to_thread(entered.wait, 3)
        fail.set()
        await shell.job
        release.set()
        await paste
        assert shell.composer.text == "original prompt"
        assert shell.images == (original,)


@pytest.mark.anyio
@pytest.mark.parametrize(("rows", "columns"), [(2, 20), (6, 24), (24, 100)])
async def test_small_terminal_keeps_composer_without_reserving_large_panels(
    rows: int, columns: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prompt_toolkit.data_structures import Size

    output = DummyOutput()
    monkeypatch.setattr(output, "get_size", lambda: Size(rows=rows, columns=columns))
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        shell = CliShell(CliRequest())
        shell.start_setup()
        task = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(3):
                while shell.composer.window.render_info is None:
                    await asyncio.sleep(0.01)
            assert shell.composer.window.render_info.window_height >= 1
            if rows >= 6:
                assert shell.output_window.render_info.window_height >= rows // 3
            shell.app.exit()
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_first_page_key_scrolls_visible_viewport_and_freezes_new_output() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        for index in range(80):
            shell.emit(f"line {index}")
        task = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(3):
                while shell.output_window.render_info is None:
                    await asyncio.sleep(0.01)
            initial = shell.output_window.vertical_scroll
            pipe.send_text("\x1b[5~")
            async with asyncio.timeout(3):
                while shell.output_window.vertical_scroll == initial:
                    await asyncio.sleep(0.01)
            assert shell.output_window.vertical_scroll < initial
            frozen = shell.output_window.vertical_scroll
            shell.emit("new output")
            await asyncio.sleep(0.1)
            assert shell.output_window.vertical_scroll == frozen
            pipe.send_text("\x1b[1;5F")
            async with asyncio.timeout(3):
                while not shell.view.follow or shell.output_window.vertical_scroll == frozen:
                    await asyncio.sleep(0.01)
            shell.app.exit()
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
