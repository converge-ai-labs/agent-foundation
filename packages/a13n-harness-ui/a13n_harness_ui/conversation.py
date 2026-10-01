"""Deterministic, bounded conversation excerpts; never continuation authority."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from a13n_harness import HarnessEvent, HarnessExtensionEvent, HarnessRunResult
from a13n_harness.content import ContentItem, ContentMetadata, request_input_content
from a13n_harness.events import InputMediaEvent, InputTextEvent
from a13n_stream_protocol.messages import project_input_content
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    PartEndEvent,
    TextPart,
    UserContent,
)

EXCERPT_LIMIT = 2048


class ConversationExcerpt(BaseModel):
    """Small saved display values, independent of model-history compaction."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    first_input: str = Field(default="", max_length=512)
    latest_input: str = Field(default="", max_length=EXCERPT_LIMIT)
    latest_reply: str = Field(default="", max_length=EXCERPT_LIMIT)
    reply_kind: Literal["none", "progress", "final"] = "none"


def excerpt_text(text: str, limit: int = EXCERPT_LIMIT) -> str:
    # Discard controls and normalize whitespace, but stop once truncation is
    # certain. A bounded preview must not scan a multi-megabyte tool answer.
    characters: list[str] = []
    pending_space = False
    for char in text:
        if char.isspace():
            pending_space = bool(characters)
        elif char.isprintable():
            if pending_space:
                characters.append(" ")
                pending_space = False
            characters.append(char)
            if limit > 0 and len(characters) > limit:
                break
    normalized = "".join(characters)
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def input_excerpt(content: Iterable[UserContent | ContentItem]) -> str:
    return _projected_excerpt(projected for item in content if (projected := project_input_content(item)) is not None)


def _projected_excerpt(content: Iterable[tuple[str | dict, ContentMetadata]]) -> str:
    text: list[str] = []
    attachments: list[str] = []
    for value, metadata in content:
        extra = metadata.model_extra or {}
        if not metadata.display or extra.get("a13n.steering-source") in {"background_process", "async_subagent"}:
            continue
        attachment = extra.get("harness_ui")
        if isinstance(attachment, dict) and isinstance(attachment.get("attachment"), dict):
            name = attachment["attachment"].get("name")
            if isinstance(name, str) and name not in attachments:
                attachments.append(name)
        elif isinstance(value, str):
            if value.strip():
                text.append(excerpt_text(value))
        elif metadata.media:
            media_type = value.get("media_type", "media")
            attachments.append(f"[{media_type}]")
    return excerpt_text(" ".join(text or attachments))


def checkpoint_excerpt(previous: ConversationExcerpt, history: Iterable[object]) -> ConversationExcerpt:
    """Derive display excerpts from a complete checkpoint without waiting for stream delivery."""
    value = previous
    for message in history:
        if isinstance(message, ModelRequest):
            if (message.metadata or {}).get("a13n.context") in {"handoff", "compaction"}:
                continue
            text = input_excerpt(request_input_content(message))
            if text:
                value = ConversationExcerpt(first_input=value.first_input or excerpt_text(text, 512), latest_input=text)
        elif isinstance(message, ModelResponse):
            text = excerpt_text(" ".join(part.content for part in message.parts if isinstance(part, TextPart)))
            if text:
                value = value.model_copy(update={"latest_reply": text, "reply_kind": "progress"})
    return value


class ExcerptCollector:
    """Observe root source events before best-effort live delivery; commit only with state."""

    def __init__(self, previous: ConversationExcerpt, *, run_id: str) -> None:
        self.value = previous
        self.run_id = run_id
        self.changed = False
        self._reply = ""
        self._input_id: str | None = None
        self._input_parts: list[tuple[str | dict, ContentMetadata]] = []
        self._first_group = False

    def _input(self, event: InputTextEvent | InputMediaEvent) -> None:
        if event.source not in {"user", "steering"} or not event.metadata.display:
            return
        if event.input_id != self._input_id:
            self._input_id = event.input_id
            self._input_parts = []
            self._first_group = not self.value.first_input
        content = excerpt_text(event.content) if isinstance(event.content, str) else event.content
        self._input_parts.append((content, event.metadata))
        text = _projected_excerpt(self._input_parts)
        if not text:
            return
        self.value = ConversationExcerpt(
            first_input=excerpt_text(text, 512) if self._first_group else self.value.first_input,
            latest_input=text,
        )
        self._reply = ""
        self.changed = True

    def observe(self, item: object) -> None:
        if not isinstance(item, HarnessEvent) or item.run_id != self.run_id:
            return
        event = item.event
        if isinstance(event, InputTextEvent | InputMediaEvent):
            self._input(event)
        elif isinstance(event, HarnessExtensionEvent):
            if isinstance(event.payload, dict) and event.payload.get("type") == "model_request_started":
                self._reply = ""
        elif isinstance(event, PartEndEvent) and isinstance(event.part, TextPart):
            self._reply = excerpt_text(self._reply + " " + event.part.content)
            if self._reply:
                self.value = self.value.model_copy(update={"latest_reply": self._reply, "reply_kind": "progress"})
                self.changed = True

    def finish(self, result: HarnessRunResult[str] | None) -> ConversationExcerpt:
        if result is not None and result.status == "completed" and isinstance(result.output, str):
            text = excerpt_text(result.output)
            if text:
                self.value = self.value.model_copy(update={"latest_reply": text, "reply_kind": "final"})
                self.changed = True
        return self.value
