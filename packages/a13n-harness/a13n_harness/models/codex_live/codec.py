"""Codex Frameless Bidi JSON, kept separate from the public Live API dialect."""

import json
from collections.abc import Iterator, Sequence
from typing import Any, Literal

from .types import CodexLiveError, CodexLiveEvent, ConversationItem, Delegation, SessionReady, Transcript

MAX_EVENT_BYTES = 256 * 1024
MAX_CONTEXT_CHARS = 32_000


def session_payload(instructions: str, voice: str, items: Sequence[ConversationItem]) -> dict[str, Any]:
    session: dict[str, Any] = {
        # Codex V3 supplies this runtime preset even when the user has no override.
        "model": "gpt-live-1-codex",
        "instructions": instructions,
        "audio": {"output": {"voice": voice}},
        "delegation": {"type": "client"},
    }
    if items:
        session["initial_items"] = [
            {
                "type": "message",
                "role": item.role,
                "content": [{"type": "output_text" if item.role == "assistant" else "input_text", "text": item.text}],
            }
            for item in items
        ]
    return session


def context_messages(
    text: str, channel: Literal["speakable", "commentary"], delegation_id: str | None
) -> Iterator[str]:
    # The Codex endpoint limits each append to 500 UTF-8 bytes, not tokens.
    encoded = text.encode("utf-8")
    while encoded:
        end = min(500, len(encoded))
        while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        chunk, encoded = encoded[:end].decode("utf-8"), encoded[end:]
        message: dict[str, Any] = {
            "type": "delegation.context.append" if delegation_id else "session.context.append",
            "channel": channel,
            "content": [{"type": "input_text", "text": chunk}],
        }
        if delegation_id:
            message["delegation_item_id"] = delegation_id
        yield json.dumps(message, ensure_ascii=False)


class EventDecoder:
    """Reconcile final transcripts per role and retain bounded delegation context."""

    def __init__(self) -> None:
        self._pending: dict[str, int] = {}
        self._context: list[ConversationItem] = []

    def _transcript(self, role: Literal["user", "assistant"], text: str, *, final: bool) -> Transcript:
        index = self._pending.get(role)
        if index is None:
            index = len(self._context)
            self._context.append(ConversationItem(role, ""))
            self._pending[role] = index
        value = text if final else self._context[index].text + text
        self._context[index] = ConversationItem(role, value[-MAX_CONTEXT_CHARS:])
        if final:
            self._pending.pop(role, None)
        while sum(len(item.text) for item in self._context) > MAX_CONTEXT_CHARS or len(self._context) > 64:
            self._context.pop(0)
            self._pending = {key: position - 1 for key, position in self._pending.items() if position > 0}
        return Transcript(role, value, final)

    def decode(self, raw: str | bytes) -> CodexLiveEvent | None:
        if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_EVENT_BYTES:
            raise CodexLiveError("Live event exceeds the transport limit")
        try:
            event = json.loads(raw)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError
            kind = event["type"]
            if kind in {"session.started", "session.updated"}:
                return SessionReady()
            if kind == "error":
                raise CodexLiveError("The Codex Live provider reported an error")
            if kind in {"input_transcript.added", "output_transcript.added"}:
                role: Literal["user", "assistant"] = "user" if kind == "input_transcript.added" else "assistant"
                text = _text(event["item"]["text"])
                return self._transcript(role, text, final=False)
            if kind == "turn.done":
                role = event["turn"]["role"]
                if role not in {"user", "assistant"}:
                    return None
                text = _text(event["turn"]["transcript"])
                return self._transcript(role, text, final=True)
            if kind == "delegation.created":
                item = event["item"]
                if item["type"] != "delegation" or item["target"] != "client":
                    return None
                item_id = _text(item["id"])
                content = item["content"]
                if not item_id or not isinstance(content, list):
                    raise ValueError
                text = "".join(_text(part["text"]) for part in content if part["type"] == "input_text")
                if not text.strip():
                    raise ValueError
                context = tuple(self._context)
                self._context.clear()
                self._pending.clear()
                return Delegation(item_id, text, context)
            return None
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise CodexLiveError("Malformed Codex Live event") from None


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value
