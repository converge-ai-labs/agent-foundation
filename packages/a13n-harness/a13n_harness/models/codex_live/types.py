"""Application-facing events for Codex's client-delegated voice transport."""

from dataclasses import dataclass, field
from typing import Literal

# Codex Frameless Bidi (V3) voices; V2 voices such as marin and cedar are incompatible.
type CodexLiveVoice = Literal[
    "juniper",
    "maple",
    "spruce",
    "ember",
    "vale",
    "breeze",
    "arbor",
    "sol",
    "cove",
    "aube",
    "bubbie",
    "haetsal",
    "hanul",
    "himari",
    "leher",
    "miko",
    "neer",
    "porto",
    "rindo",
    "rio",
    "rivage",
    "selva",
    "sonna",
    "tinta",
    "viola",
]


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
