"""Run-local lexical search; bounded excerpts and a checkpointed per-message retention ledger."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

import jieba
from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, cast, false, func
from sqlalchemy.dialects.postgresql import ARRAY, TSQUERY

from a13n_service.infra.errors import invalid
from a13n_service.infra.telemetry import meter

if TYPE_CHECKING:
    from a13n_stream_protocol.display import Item
    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import InstrumentedAttribute

    from a13n_service.settings import RunSearch

logger = get_logger(__name__)
TRUNCATED = meter.create_counter(
    "a13n.run_search.excerpts_truncated", description="Prepared search excerpts shortened to their UTF-8 budget"
)
EVICTED = meter.create_counter(
    "a13n.run_search.messages_evicted", description="Messages removed while preparing bounded search vectors"
)

_TOKENIZER = jieba.Tokenizer()
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|_+")


def initialize() -> None:
    """Load the pinned package dictionary once, before opening request transactions."""
    _TOKENIZER.initialize()


def words(value: str, *, document: bool) -> tuple[str, ...]:
    value = unicodedata.normalize("NFKC", value)
    normalized = value.casefold()
    cut = _TOKENIZER.cut_for_search(normalized, HMM=False) if document else _TOKENIZER.cut(normalized, HMM=False)
    terms = {term for term in cut if any(char.isalnum() for char in term)}
    # Keep the complete identifier on both sides. Components are additional document terms, not extra ANDs.
    for match in _IDENTIFIER.finditer(value):
        terms.add(match[0].casefold())
        if document:
            terms.update(part.casefold() for part in _CAMEL.split(match[0]) if part)
    return tuple(sorted(term for term in terms if "\x00" not in term and len(term.encode()) < 2048))


def excerpt(value: str, head: int, tail: int) -> tuple[str, ...]:
    """Keep UTF-8 boundaries; tokenize the separated ends independently."""
    data = value.encode()
    if len(data) <= head + tail:
        return (value,)
    return (data[:head].decode(errors="ignore"), data[-tail:].decode(errors="ignore"))


def excerpt_words(value: str, head: int, tail: int) -> tuple[str, ...]:
    return tuple(sorted({term for part in excerpt(value, head, tail) for term in words(part, document=True)}))


def vector(terms: Iterable[str]) -> ColumnElement[str]:
    """Exact lexemes with no positions, weights or server-side parser differences."""
    return func.array_to_tsvector(cast(list(terms), ARRAY(String)))


def predicate(column: InstrumentedAttribute[str], value: str | None) -> ColumnElement[bool] | None:
    if value is None:
        return None
    if len(value) > 1024:
        raise invalid("text_query", "Use at most 1024 characters")
    terms = words(value, document=False)
    if len(terms) > 64:
        raise invalid("text_query", "Use at most 64 distinct search terms")
    if not terms:
        return false()
    # This is a bound value, not SQL. Escape tsquery literal syntax too, so operators stay ordinary input.
    query = " & ".join("'" + term.replace("\\", "\\\\").replace("'", "''") + "'" for term in terms)
    return column.bool_op("@@")(cast(query, TSQUERY))


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    role: Literal["user", "assistant"]
    ordinal: int = 0
    terms: tuple[str, ...]
    # Older user inputs can shrink without retaining a second raw-text copy.
    compact: tuple[str, ...] = ()


class SearchState(BaseModel):
    """Private tail metadata. No raw text; final display ordinals never get indexed twice after eviction."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[Message, ...] = ()
    through: int = 0
    pending: tuple[int, ...] = ()


class Index:
    """One attempt's committed cache. Preparing a candidate never mutates the committed instance."""

    def __init__(self, state: SearchState, limits: RunSearch) -> None:
        self.state, self.limits = state, limits
        self.messages = {message.key: message for message in state.messages}
        self.counts: Counter[str] = Counter(term for message in state.messages for term in message.terms)
        self.bytes = 8 + sum(4 + len(term.encode()) for term in self.counts)
        self.changed = False

    def _replace(self, message: Message | None, key: str) -> None:
        old = self.messages.get(key)
        if old is not None:
            for term in old.terms:
                self.counts[term] -= 1
                if self.counts[term] == 0:
                    del self.counts[term]
                    self.bytes -= 4 + len(term.encode())
        if message is None:
            self.messages.pop(key, None)
        else:
            self.messages[key] = message
            for term in message.terms:
                if term not in self.counts:
                    self.bytes += 4 + len(term.encode())
                self.counts[term] += 1

    def _user(self, key: str, text: str) -> None:
        if key in self.messages or not text:
            return
        limits = self.limits
        if len(text.encode()) > limits.user_head_bytes + limits.user_tail_bytes:
            TRUNCATED.add(1, {"role": "user"})
        self._replace(
            Message(
                key=key,
                role="user",
                terms=excerpt_words(text, limits.user_head_bytes, limits.user_tail_bytes),
                compact=excerpt_words(text, limits.user_head_bytes // 4, limits.user_tail_bytes // 4),
            ),
            key,
        )

    def prepare(self, items: Iterable[Item], inputs: Iterable[tuple[str, str]]) -> Index:
        candidate = object.__new__(Index)
        candidate.state, candidate.limits = self.state, self.limits
        candidate.messages, candidate.counts = self.messages.copy(), self.counts.copy()
        candidate.bytes = self.bytes
        through, pending = self.state.through, set(self.state.pending)
        for key, text in inputs:
            candidate._user(key, text)
        for item in items:
            if item.ordinal <= self.state.through and item.ordinal not in pending:
                continue
            through = max(through, item.ordinal)
            if item.state == "in_progress":
                pending.add(item.ordinal)
                continue
            pending.discard(item.ordinal)
            content = item.content
            if item.kind != "text_message" or content.get("role") != "assistant" or content.get("subagentRunId"):
                continue
            metadata = content.get("metadata")
            if isinstance(metadata, dict) and metadata.get("display") is False:
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                if len(text.encode()) > self.limits.assistant_head_bytes + self.limits.assistant_tail_bytes:
                    TRUNCATED.add(1, {"role": "assistant"})
                candidate._replace(
                    Message(
                        key=item.id,
                        role="assistant",
                        ordinal=item.ordinal,
                        terms=excerpt_words(text, self.limits.assistant_head_bytes, self.limits.assistant_tail_bytes),
                    ),
                    item.id,
                )
        candidate._retain()
        candidate.state = SearchState(
            messages=tuple(candidate.messages.values()), through=through, pending=tuple(sorted(pending))
        )
        candidate.changed = candidate.counts.keys() != self.counts.keys()
        return candidate

    def _retain(self) -> None:
        limits = self.limits
        if self.bytes <= limits.vector_bytes and len(self.messages) <= limits.messages:
            return
        users = [message for message in self.messages.values() if message.role == "user"]
        assistants = sorted(
            (message for message in self.messages.values() if message.role == "assistant"),
            key=lambda message: message.ordinal,
        )
        removed, shrunk = 0, 0

        def fits() -> bool:
            return self.bytes <= limits.target_bytes and len(self.messages) <= limits.messages

        for message in assistants[:-1]:
            self._replace(None, message.key)
            removed += 1
            if fits():
                break
        if not fits():
            for message in users[1:-1]:
                self._replace(message.model_copy(update={"terms": message.compact}), message.key)
                shrunk += 1
                if fits():
                    break
        if not fits():
            for message in users[1:-1]:
                self._replace(None, message.key)
                removed += 1
                if fits():
                    break
        if not fits():
            raise ValueError("Protected search excerpts exceed the configured vector budget")
        EVICTED.add(removed)
        logger.info(
            "Run search retention applied", extra={"vector_bytes": self.bytes, "removed": removed, "shrunk": shrunk}
        )


def seed(text: str, key: str, limits: RunSearch) -> Index:
    index = Index(SearchState(), limits)
    index._user(key, text)
    index.state = SearchState(messages=tuple(index.messages.values()))
    return index
