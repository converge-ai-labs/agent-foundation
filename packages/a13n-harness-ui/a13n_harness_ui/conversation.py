"""Deterministic, bounded conversation excerpts; never continuation authority."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from a13n_harness import HarnessEvent, HarnessExtensionEvent, HarnessRunResult
from a13n_harness.model_context import ModelInputEvent, user_prompt_content
from a13n_stream_protocol.messages import project_input_content
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import (
    EnqueuedMessagesEvent,
    ModelRequest,
    PartEndEvent,
    TextPart,
    UserContent,
    UserPromptPart,
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
    # Discard terminal controls and normalize whitespace, not the user's meaning.
    text = " ".join("".join(char for char in text if char.isprintable() or char.isspace()).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def input_excerpt(content: Iterable[UserContent]) -> str:
    text: list[str] = []
    attachments: list[str] = []
    for item in content:
        projected = project_input_content(item)
        if projected is None:
            continue
        value, metadata = projected
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


class ExcerptCollector:
    """Observe root source events before best-effort live delivery; commit only with state."""

    def __init__(self, previous: ConversationExcerpt, *, run_id: str) -> None:
        self.value = previous
        self.run_id = run_id
        self.changed = False
        self._reply = ""

    def _input(self, content: Iterable[UserContent]) -> None:
        text = input_excerpt(content)
        if not text:
            return
        self.value = ConversationExcerpt(
            first_input=self.value.first_input or excerpt_text(text, 512),
            latest_input=text,
        )
        self._reply = ""
        self.changed = True

    def observe(self, item: object) -> None:
        if not isinstance(item, HarnessEvent) or item.run_id != self.run_id:
            return
        event = item.event
        if isinstance(event, ModelInputEvent):
            self._input(event.content)
        elif isinstance(event, EnqueuedMessagesEvent):
            for message in event.messages:
                if isinstance(message, ModelRequest):
                    self._input(
                        content
                        for part in message.parts
                        if isinstance(part, UserPromptPart)
                        for content in user_prompt_content(part)
                    )
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
