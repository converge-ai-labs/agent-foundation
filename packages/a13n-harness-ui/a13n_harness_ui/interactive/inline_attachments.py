"""Atomic editor attachments; labels are presentation, never attachment identity."""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.clipboard import InMemoryClipboard
from prompt_toolkit.completion import Completer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput

from a13n_harness_ui.thread_files import (
    MAX_ATTACHMENTS,
    MAX_INPUT_BYTES,
    AttachmentUpload,
    ComposerAttachment,
    ComposerInput,
    composer_attachment_label,
)


@dataclass
class PendingAttachment:
    number: int
    upload: AttachmentUpload | None = None
    error: str | None = None

    @property
    def label(self) -> str:
        kind = "file" if self.upload and not (self.upload.media_type or "").startswith("image/") else "image"
        return f"{kind}#{self.number}"

    @property
    def display(self) -> str:
        state = ": failed" if self.error else ": loading" if self.upload is None else ""
        label = composer_attachment_label(self.label, self.upload.name if self.upload else None)
        return f"[{label}{state}]"


class InlineAttachments:
    def __init__(self) -> None:
        self.values: dict[str, PendingAttachment] = {}
        self._next_token = 0xE000
        self._next_number = 1

    def reserve(self) -> str:
        if self._next_token > 0xF8FF:
            raise ValueError("Attachment editor capacity reached. Start a new terminal session.")
        token = chr(self._next_token)
        self._next_token += 1
        self.values[token] = PendingAttachment(self._next_number)
        self._next_number += 1
        return token

    @staticmethod
    def external_text(text: str) -> str:
        # Private editor characters from terminal input cannot acquire identity.
        # Keep them visible as escapes instead of silently treating them as media.
        return "".join(f"\\u{ord(c):04x}" if 0xE000 <= ord(c) <= 0xF8FF else c for c in text)

    def tokens(self, text: str) -> list[str]:
        return [c for c in text if c in self.values]

    def display(self, text: str) -> str:
        return "".join(self.values[c].display if c in self.values else c for c in text)

    def uploads(self, text: str) -> tuple[AttachmentUpload, ...]:
        return tuple(upload for c in self.tokens(text) if (upload := self.values[c].upload) is not None)

    def compile(self, text: str, source_id: str | None = None) -> ComposerInput:
        parts: list[str | ComposerAttachment] = []
        start = 0
        for index, char in enumerate(text):
            entry = self.values.get(char)
            if entry is None:
                if 0xE000 <= ord(char) <= 0xF8FF:
                    raise ValueError("This attachment is no longer available. Delete its marker and paste again.")
                continue
            if entry.error:
                raise ValueError(f"{entry.display}: {entry.error} Delete this attachment and paste again.")
            if entry.upload is None:
                raise ValueError("Still reading an attachment. Your draft is preserved.")
            if index > start:
                parts.append(text[start:index])
            parts.append(ComposerAttachment(entry.upload, entry.label))
            start = index + 1
        if start < len(text):
            parts.append(text[start:])
        value = ComposerInput(tuple(parts), source_id=source_id)
        if len(value.attachments) > MAX_ATTACHMENTS:
            raise ValueError("An input supports up to eight attachments.")
        if sum(len(item.data) for item in value.attachments) > MAX_INPUT_BYTES:
            raise ValueError("An input supports up to 20 MiB of attachments.")
        return value

    def retain(self, texts: tuple[str, ...]) -> None:
        reachable = set("".join(texts))
        self.values = {token: value for token, value in self.values.items() if token in reachable}


class AttachmentClipboard(InMemoryClipboard):
    """Expose native kill-ring roots without duplicating its bounded yank behavior."""

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(item.text for item in self._ring)


class AttachmentBuffer(Buffer):
    """Keep native movement/selection/undo, separating external insertion from tokens."""

    def __init__(self, attachments: InlineAttachments, completer: Completer | None = None) -> None:
        self.attachments = attachments
        super().__init__(multiline=True, completer=completer, complete_while_typing=True)

    def set_document(self, value: Document, bypass_readonly: bool = False) -> None:
        super().set_document(value, bypass_readonly=bypass_readonly)
        # Native Buffer restores text/cursor only. Draft snapshots and async
        # anchor replacement also need the original selection.
        self.selection_state = value.selection

    def insert_text(
        self, data: str, overwrite: bool = False, move_cursor: bool = True, fire_event: bool = True
    ) -> None:
        super().insert_text(self.attachments.external_text(data), overwrite, move_cursor, fire_event)

    def insert_attachment(self, token: str) -> None:
        self.save_to_undo_stack()
        if self.selection_state is not None:
            self.cut_selection()
        super().insert_text(token, fire_event=False)

    def replace_token(self, token: str, replacement: str) -> None:
        """Resolve an asynchronous anchor without moving the user's current selection."""
        document = self.document
        text = document.text
        if token not in text:
            return
        self.save_to_undo_stack()

        def position(offset: int) -> int:
            return offset + text[:offset].count(token) * (len(replacement) - 1)

        selection = document.selection
        if selection is not None:
            from prompt_toolkit.selection import SelectionState

            selection = SelectionState(position(selection.original_cursor_position), selection.type)
        self.document = Document(text.replace(token, replacement), position(document.cursor_position), selection)


class AttachmentProcessor(Processor):
    def __init__(self, attachments: InlineAttachments) -> None:
        self.attachments = attachments

    def apply_transformation(self, transformation_input: TransformationInput) -> Transformation:
        fragments: StyleAndTextTuples = []
        source_to_display = [0]
        display_to_source: list[int] = []
        source = 0
        for fragment in transformation_input.fragments:
            style, text = fragment[:2]
            for char in text:
                entry = self.attachments.values.get(char)
                displayed = entry.display if entry else char
                token_style = ("class:input-area.attachment " if entry else "") + style
                fragments.append((token_style, displayed))
                # Clicks inside a label snap to an edge; selection styling expands
                # with the original character rather than disappearing.
                display_to_source.extend(
                    source if i < len(displayed) / 2 else source + 1 for i in range(len(displayed))
                )
                source_to_display.append(source_to_display[-1] + len(displayed))
                source += 1
        display_to_source.append(source)
        return Transformation(
            fragments,
            source_to_display=lambda offset: source_to_display[min(offset, source)],
            display_to_source=lambda offset: display_to_source[min(offset, len(display_to_source) - 1)],
        )
