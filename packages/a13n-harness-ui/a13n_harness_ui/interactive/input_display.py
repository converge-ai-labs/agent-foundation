"""Shared TUI projection of ordered composer parts, live and from retained history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness_ui.thread_files import composer_attachment_label

if TYPE_CHECKING:
    from a13n_stream_protocol import ContentMetadata

    from a13n_harness_ui.surfaces import TranscriptPart


def composer_piece(metadata: ContentMetadata) -> tuple[int, str | None] | None:
    if not metadata.source_id:
        return None
    namespace = (metadata.model_extra or {}).get("harness_ui")
    if not isinstance(namespace, dict):
        return None
    composer = namespace.get("composer")
    if not isinstance(composer, dict):
        return None
    index, label = composer.get("index"), composer.get("label")
    if not isinstance(index, int) or index < 0 or (label is not None and not isinstance(label, str)):
        return None
    attachment = namespace.get("attachment")
    name = attachment.get("name") if isinstance(attachment, dict) else None
    if label is not None:
        label = composer_attachment_label(label, name if isinstance(name, str) else None)
    return index, label


def composer_history(parts: tuple[TranscriptPart, ...]) -> list[TranscriptPart]:
    result: list[TranscriptPart] = []
    source_id: str | None = None
    seen: set[int] = set()
    for part in parts:
        if not part.metadata.display:
            continue
        piece = composer_piece(part.metadata) if part.kind in {"user", "media"} else None
        if piece is None:
            source_id = None
            result.append(part)
            continue
        index, label = piece
        text = f"[{label}]" if label else part.text or ""
        if source_id != part.metadata.source_id:
            source_id = part.metadata.source_id
            seen = set()
            result.append(part.model_copy(update={"kind": "user", "text": ""}))
        if index not in seen:
            previous = result[-1]
            result[-1] = previous.model_copy(
                update={
                    "text": (previous.text or "") + text,
                    "text_truncated": previous.text_truncated or part.text_truncated,
                    "value_omitted": previous.value_omitted or part.value_omitted,
                }
            )
            seen.add(index)
    return result
