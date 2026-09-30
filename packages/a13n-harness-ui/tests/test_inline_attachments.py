from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from io import BytesIO

import pytest
from a13n_harness import HarnessEvent
from a13n_harness.model_context import ModelInputEvent
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.history import restore_transcript
from a13n_harness_ui.interactive.inline_attachments import AttachmentBuffer, AttachmentProcessor, InlineAttachments
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.surfaces import TranscriptEntry, TranscriptPage, TranscriptPart
from a13n_harness_ui.thread_files import AttachmentUpload, ComposerAttachment, composer_attachment_label
from a13n_stream_protocol import HarnessAguiObserver
from a13n_stream_protocol.messages import project_input_content
from PIL import Image
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.processors import TransformationInput
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.selection import SelectionState
from pydantic_ai.messages import BinaryContent, TextContent


def _image() -> AttachmentUpload:
    stream = BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    return AttachmentUpload("clipboard.png", stream.getvalue(), "image/png")


def _source(renderer: StreamRenderer) -> str:
    return "\n".join(block.source for block in renderer.transcript.blocks.values())


async def _until(predicate) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


def test_ordered_tokens_are_not_labels_and_numbering_is_stable() -> None:
    registry = InlineAttachments()
    first, second = registry.reserve(), registry.reserve()
    image = _image()
    registry.values[first].upload = registry.values[second].upload = image
    value = registry.compile(f"before {first} between {second} after")
    assert value.parts == (
        "before ",
        ComposerAttachment(image, "image#1"),
        " between ",
        ComposerAttachment(image, "image#2"),
        " after",
    )
    assert registry.display(f"only {second}") == "only [image#2]"
    assert not registry.compile("[image#1]").attachments
    assert not registry.compile(registry.external_text(first)).attachments
    third = registry.reserve()
    assert third not in (first, second)
    assert registry.values[third].number == 3
    with pytest.raises(ValueError, match="eight"):
        registry.compile(first * 9)


def test_buffer_atomic_edit_undo_and_external_private_characters() -> None:
    registry = InlineAttachments()
    token = registry.reserve()
    registry.values[token].upload = _image()
    buffer = AttachmentBuffer(registry)
    buffer.insert_text("before ", fire_event=False)
    buffer.insert_attachment(token)
    buffer.insert_text(" after", fire_event=False)
    buffer.cursor_position = 8
    buffer.save_to_undo_stack()
    assert buffer.delete_before_cursor() == token
    buffer.undo()
    assert registry.display(buffer.text) == "before [image#1] after"
    assert registry.compile(buffer.text).attachments == (_image(),)
    buffer.redo()
    assert not registry.compile(buffer.text).attachments
    buffer.insert_text(token, fire_event=False)
    assert token not in buffer.text
    assert "\\ue000" in buffer.text


def test_processor_maps_whole_label_and_preserves_selection_style() -> None:
    registry = InlineAttachments()
    token = registry.reserve()
    registry.values[token].upload = _image()
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        processor = AttachmentProcessor(registry)
        transformed = processor.apply_transformation(
            TransformationInput(
                buffer_control=shell.composer.control,
                document=Document(f"中{token}文"),
                lineno=0,
                source_to_display=lambda i: i,
                fragments=[("", "中"), ("class:selected", token), ("", "文")],
                width=40,
                height=3,
            )
        )
        assert "".join(fragment[1] for fragment in transformed.fragments) == "中[image#1]文"
        assert transformed.source_to_display(1) == 1
        assert transformed.source_to_display(2) == 10
        assert transformed.display_to_source(3) == 1
        assert transformed.display_to_source(8) == 2
        assert "class:selected" in transformed.fragments[1][0]
        shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_real_keys_delete_undo_yank_and_literal_paste() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.composer.buffer.document = Document("before  after", 7)
        shell.insert_attachments((_image(),))
        token = shell.inline.tokens(shell.composer.text)[0]
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text("\x7f")
            await _until(lambda: token not in shell.composer.text)
            pipe.send_text("\x1f")  # Ctrl+_: native undo.
            await _until(lambda: token in shell.composer.text)
            assert len(shell.images) == 1
            pipe.send_text("\x15")  # Ctrl+U cuts through the token to the line start.
            await _until(lambda: token not in shell.composer.text)
            pipe.send_text("\x19")  # Ctrl+Y restores the native clipboard attachment.
            await _until(lambda: token in shell.composer.text)
            assert len(shell.images) == 1
            pipe.send_text("\x1b[D")  # One left crosses the complete label.
            await _until(lambda: shell.composer.buffer.cursor_position == 7)
            pipe.send_text("\x1b[3~")
            await _until(lambda: token not in shell.composer.text)
            pipe.send_text("\x1b[200~[image#1]" + token + "\x1b[201~")
            await _until(lambda: "[image#1]" in shell.composer.text)
            assert not shell.images
            assert token not in shell.composer.text
        finally:
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
@pytest.mark.parametrize("removed", [False, True])
async def test_async_paste_stays_at_original_anchor(monkeypatch, removed: bool) -> None:
    import a13n_harness_ui.interactive.shell as module

    entered, release = threading.Event(), threading.Event()

    def clipboard():
        entered.set()
        release.wait(3)
        return (_image(),)

    monkeypatch.setattr(module, "clipboard_images", clipboard)
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.composer.buffer.document = Document("before  after", 7)
        shell.start_clipboard()
        token = shell.inline.tokens(shell.composer.text)[0]  # Synchronous insertion.
        with pytest.raises(ValueError, match="Still reading"):
            shell.inline.compile(shell.composer.text)
        await asyncio.to_thread(entered.wait, 2)
        if removed:
            shell.composer.buffer.delete_before_cursor()
        shell.composer.buffer.insert_text("typed later", fire_event=False)
        release.set()
        await shell._clipboard_task
        if removed:
            assert token not in shell.composer.text
            assert not shell.images
        else:
            assert shell.inline.display(shell.composer.text) == "before [image#1]typed later after"
            assert shell.composer.buffer.cursor_position == 19
        shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_async_multi_image_resolution_preserves_cursor_and_selection(monkeypatch) -> None:
    import a13n_harness_ui.interactive.shell as module

    monkeypatch.setattr(module, "clipboard_images", lambda: (_image(), _image()))
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.composer.buffer.document = Document("before  after", 7)
        anchor = shell._begin_attachment()
        shell.composer.buffer.document = Document(shell.composer.text, len(shell.composer.text), SelectionState(8))
        await shell.acquire_images(anchor=anchor)
        assert shell.inline.display(shell.composer.text) == "before [image#1][image#2] after"
        assert shell.composer.buffer.cursor_position == len(shell.composer.text)
        assert len(shell.images) == 2
        assert shell.composer.buffer.selection_state is not None
        assert shell.composer.buffer.selection_state.original_cursor_position == 9
        shell.renderer.transcript.close()


def test_live_and_history_combine_parts_without_internal_details_or_duplicate_echo() -> None:
    def metadata(index: int, label: str | None = None, name: str | None = None):
        return {
            "source_id": "input-inline",
            "harness_ui": {"composer": {"index": index, "label": label}, "attachment": {"name": name}},
        }

    from a13n_harness import ContentItem, ContentMetadata

    content = [
        TextContent("before " + "x" * 17000, metadata=metadata(0)),
        TextContent("internal path", metadata={**metadata(1), "display": False}),
        ContentItem(
            BinaryContent(b"private pixels", media_type="image/png"),
            ContentMetadata.model_validate(metadata(1, "image#1", "clipboard.png")),
        ),
        TextContent(" using ", metadata=metadata(2)),
        TextContent("internal file path and metadata", metadata=metadata(3, "file#2", "requirements.md")),
        TextContent(" after", metadata=metadata(4)),
    ]
    event = HarnessEvent(
        thread_id="thread-inline",
        run_id="run-inline",
        sequence=1,
        occurred_at=datetime.now(UTC),
        event=ModelInputEvent(content=content),
    )
    observer = HarnessAguiObserver()
    renderer = StreamRenderer(Status())
    events = observer.observe(event)
    for item in events:
        renderer.ingest(item.type.value, item.model_dump(mode="json"))
    expected = "> before " + "x" * 17000 + "[image#1] using [file#2: requirements.md] after"
    assert _source(renderer) == expected
    for item in events:
        renderer.ingest(item.type.value, item.model_dump(mode="json"))
    assert _source(renderer) == expected
    local = StreamRenderer(Status())
    local.local_input("input-inline", expected[2:])
    for item in events:
        local.ingest(item.type.value, item.model_dump(mode="json"))
    assert _source(local) == expected
    parts = []
    for item in content:
        value, meta = project_input_content(item)
        parts.append(
            TranscriptPart(
                kind="media" if meta.media else "user",
                text=value if isinstance(value, str) else "raw media",
                metadata=meta,
            )
        )
    history = StreamRenderer(Status())
    restore_transcript(
        history,
        TranscriptPage(total=1, entries=(TranscriptEntry(position=0, message_kind="request", parts=tuple(parts)),)),
    )
    assert _source(history) == expected
    for target in (renderer, local, history):
        target.transcript.close()


@pytest.mark.anyio
async def test_kill_ring_keeps_attachments_across_command_reset() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.insert_attachments((_image(),))
        token = shell.composer.text
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text("\x15")
            await _until(lambda: not shell.composer.text)
            pipe.send_text("plain text")
            await _until(lambda: shell.composer.text == "plain text")
            pipe.send_text("\x15")
            await _until(lambda: not shell.composer.text)
            pipe.send_text("/help\r")
            await _until(lambda: "Command accepted: /help" in _source(shell.renderer))
            pipe.send_text("\x19")
            await _until(lambda: shell.composer.text == "plain text")
            pipe.send_text("\x1by")
            await _until(lambda: shell.composer.text == token)
            assert shell.inline.compile(shell.composer.text).attachments == (_image(),)
        finally:
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_unknown_slash_prompt_can_include_images(monkeypatch) -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        shell.composer.buffer.insert_text("/tmp/example.png compare ", fire_event=False)
        shell.insert_attachments((_image(),))
        submitted = []

        async def handle(text, **kwargs):
            submitted.append(shell.inline.compile(text))

        monkeypatch.setattr(shell, "handle", handle)
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text("\r")
            await _until(lambda: bool(submitted))
            assert submitted[0].display_text == "/tmp/example.png compare [image#1]"
            assert submitted[0].attachments == (_image(),)
        finally:
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_failed_admission_survives_help_and_recovers_numbering() -> None:
    from weakref import ref

    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.key_binding.key_processor import KeyPress, KeyPressEvent
    from prompt_toolkit.keys import Keys

    started, fail = asyncio.Event(), asyncio.Event()

    class Backend:
        thread_id = None
        receipt_id = None

        async def execute(self, *args, **kwargs):
            started.set()
            await fail.wait()
            raise ValueError("fixture admission failure")

        async def skill_catalog(self):
            return None

        def thinking_choices(self):
            return ()

        async def interaction(self):
            return None

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = Backend()
        shell.ready = True
        shell.insert_attachments((_image(),))
        original = shell.composer.text

        async def submit() -> None:
            # Exercise the real draft retention binding without a terminal reader
            # or completion/render scheduling; real-key behavior is covered above.
            with set_app(shell.app):
                event = KeyPressEvent(ref(shell.app.key_processor), None, [KeyPress(Keys.ControlM)], [], False)
                bindings = shell._bindings().get_bindings_for_keys((Keys.ControlM,))
                assert len(bindings) == 1
                bindings[0].call(event)
            assert shell._input_task is not None
            await asyncio.wait_for(shell._input_task, timeout=3)

        try:
            await submit()
            await asyncio.wait_for(started.wait(), timeout=3)
            assert not shell.can_steer
            shell.composer.buffer.document = Document("/help", 5)
            await submit()
            assert "Command accepted: /help" in _source(shell.renderer)
            assert original in shell.inline.values
            fail.set()
            assert shell.job is not None
            await asyncio.wait_for(shell.job, timeout=3)
            assert shell.composer.text == original
            shell.insert_attachments((_image(),))
            assert shell.inline.display(shell.composer.text) == "[image#1][image#2]"
            assert len(shell.inline.compile(shell.composer.text).attachments) == 2
        finally:
            fail.set()
            if shell.job is not None:
                await asyncio.wait_for(shell.job, timeout=3)
            await shell.app.cancel_and_wait_for_background_tasks()
            shell.renderer.transcript.close()


@pytest.mark.anyio
@pytest.mark.parametrize("width", [20, 40, 80])
@pytest.mark.parametrize("file", [False, True])
async def test_rendered_wrapped_chinese_attachment_cursor_and_selection(monkeypatch, width: int, file: bool) -> None:
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.data_structures import Size

    output = DummyOutput()
    monkeypatch.setattr(output, "get_size", lambda: Size(rows=24, columns=width))
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        shell = CliShell(CliRequest())
        prefix = "中文" * (width // 4 - 1)
        shell.composer.buffer.document = Document(prefix, len(prefix))
        upload = AttachmentUpload("需求.md", b"private contents", "text/markdown") if file else _image()
        shell.insert_attachments((upload,))
        token = shell.inline.tokens(shell.composer.text)[0]
        shell.composer.buffer.insert_text("结尾", fire_event=False)
        with set_app(shell.app):
            for cursor in (len(prefix), len(prefix) + 1, len(shell.composer.text)):
                shell.composer.buffer.document = Document(shell.composer.text, cursor)
                shell.app.renderer.render(shell.app, shell.app.layout)
                info = shell.composer.window.render_info
                assert info is not None
                displayed_cursor = len(shell.inline.display(shell.composer.text[:cursor]))
                assert (0, displayed_cursor) in info._rowcol_to_yx
            shell.composer.buffer.document = Document(shell.composer.text, len(prefix) + 1, SelectionState(len(prefix)))
            shell.app.renderer.render(shell.app, shell.app.layout)
            screen = shell.app.renderer._last_screen
            assert screen is not None
            selected = "".join(
                cell.char
                for _, row in sorted(screen.data_buffer.items())
                for _, cell in sorted(row.items())
                if "selected" in cell.style
            )
            assert selected == ("[file#1: 需求.md]" if file else "[image#1]")
            assert token not in selected
        shell.renderer.transcript.close()


@pytest.mark.parametrize(
    ("name", "display_name"),
    [
        ("requirements.md", "requirements.md"),
        ("需求说明.md", "需求说明.md"),
        ("a" * 60 + ".csv", "a" * 27 + "…" + "a" * 12 + ".csv"),
        ("/private/docs/notes\n\t.txt", "notes.txt"),
        ("C:\\private\\notes.txt", "notes.txt"),
    ],
)
def test_file_name_is_display_only_and_atomic(name: str, display_name: str) -> None:
    registry = InlineAttachments()
    token = registry.reserve()
    upload = AttachmentUpload(name, b"private file contents", "text/plain")
    registry.values[token].upload = upload
    buffer = AttachmentBuffer(registry)
    buffer.insert_attachment(token)
    compiled = registry.compile(buffer.text)
    assert registry.display(buffer.text) == compiled.display_text == f"[file#1: {display_name}]"
    assert compiled.parts == (ComposerAttachment(upload, "file#1"),)
    assert compiled.attachments == (upload,)
    buffer.save_to_undo_stack()  # Native key processing saves before deletion.
    assert buffer.delete_before_cursor() == token
    assert not buffer.text
    buffer.undo()
    assert registry.compile(buffer.text) == compiled
    assert composer_attachment_label("image#2", name) == "image#2"


def test_old_file_metadata_without_name_keeps_label() -> None:
    from a13n_harness_ui.interactive.input_display import composer_piece
    from a13n_stream_protocol import ContentMetadata

    metadata = ContentMetadata.from_native(
        {"source_id": "old-input", "harness_ui": {"composer": {"index": 0, "label": "file#1"}}}
    )
    assert composer_piece(metadata) == (0, "file#1")


@pytest.mark.anyio
@pytest.mark.parametrize("keys", ["\x16", "\x1bv"])
@pytest.mark.parametrize("change", ["none", "edit", "reset"])
async def test_empty_clipboard_keys_preserve_only_valid_redo(monkeypatch, keys: str, change: str) -> None:
    import a13n_harness_ui.interactive.shell as module

    entered, release = threading.Event(), threading.Event()

    def clipboard():
        entered.set()
        release.wait(5)
        return ()

    monkeypatch.setattr(module, "clipboard_images", clipboard)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        buffer = shell.composer.buffer
        buffer.document = Document("first", 5)
        buffer.save_to_undo_stack()
        buffer.insert_text(" later", fire_event=False)
        buffer.undo()
        assert buffer.text == "first"
        assert buffer._redo_stack == [("first later", 11)]
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text(keys)
            assert await asyncio.to_thread(entered.wait, 3)
            if change == "edit":
                # Even editing back to the same text must invalidate old redo.
                pending_text = buffer.text
                buffer.insert_text("changed", fire_event=False)
                buffer.delete_before_cursor(7)
                assert buffer.text == pending_text
            elif change == "reset":
                shell._draft_generation += 1
                buffer.reset(document=Document("new draft", 9))
            release.set()
            assert shell._clipboard_task is not None
            await shell._clipboard_task
            assert not shell._pending_attachments
            assert not _source(shell.renderer)
            if change == "none":
                assert buffer._redo_stack == [("first later", 11)]
                buffer.redo()
                assert buffer.text == "first later"
            else:
                assert buffer._redo_stack == []
                buffer.redo()
                assert buffer.text == ("new draft" if change == "reset" else "first")
        finally:
            release.set()
            if shell._clipboard_task is not None:
                await shell._clipboard_task
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
@pytest.mark.parametrize("content", ["attachment", "folded-text"])
async def test_pending_selected_content_survives_interaction_submission(monkeypatch, content: str) -> None:
    import a13n_harness_ui.interactive.shell as module

    entered, release = threading.Event(), threading.Event()

    def clipboard():
        entered.set()
        release.wait(5)
        return ()

    monkeypatch.setattr(module, "clipboard_images", clipboard)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        buffer = shell.composer.buffer
        if content == "attachment":
            shell.insert_attachments((_image(),))
        else:
            buffer.insert_text(shell.pastes.insert("folded text " * 100), fire_event=False)
        original = Document(buffer.text, len(buffer.text), SelectionState(0))
        buffer.document = original
        answers = []

        async def handle(text, **kwargs):
            answers.append(text)

        monkeypatch.setattr(shell, "handle", handle)
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await _until(lambda: shell.app.is_running)
            pipe.send_text("\x16")
            assert await asyncio.to_thread(entered.wait, 3)
            shell._save_draft()
            # Model an interaction answer through the real Enter/reset/retain path.
            shell.menu_handler = handle
            pipe.send_text("answer\r")
            await _until(lambda: answers == ["answer"])
            assert shell._pending_attachments
            if content == "attachment":
                assert shell.inline.compile(original.text).attachments == (_image(),)
            else:
                assert shell.pastes.expand(original.text) == "folded text " * 100
            release.set()
            assert shell._clipboard_task is not None
            await shell._clipboard_task
            assert not shell._pending_attachments
            shell._restore_draft()
            assert buffer.document == original
            if content == "attachment":
                assert shell.inline.compile(buffer.text).attachments == (_image(),)
            else:
                assert shell.pastes.expand(buffer.text) == "folded text " * 100
            assert not _source(shell.renderer)
        finally:
            release.set()
            if shell._clipboard_task is not None:
                await shell._clipboard_task
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()
