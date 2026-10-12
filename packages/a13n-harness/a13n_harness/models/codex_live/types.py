"""Application-facing events for Codex's client-delegated voice transport."""

from dataclasses import dataclass, field
from typing import Literal


class CodexLiveError(Exception):
    """A safe transport failure, without provider payloads or credentials."""


@dataclass(frozen=True, slots=True)
class ConversationItem:
    role: Literal["user", "assistant", "developer"]
    text: str


@dataclass(frozen=True, slots=True)
class Transcript:
    role: Literal["user", "assistant"]
    text: str
    final: bool


@dataclass(frozen=True, slots=True)
class Delegation:
    item_id: str
    text: str
    context: tuple[ConversationItem, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionReady:
    type: Literal["ready"] = field(default="ready", init=False)


type CodexLiveEvent = Transcript | Delegation | SessionReady
