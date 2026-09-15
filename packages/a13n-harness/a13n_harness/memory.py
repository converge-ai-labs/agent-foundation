"""Provider-neutral memory contracts shared by embedded and hosted applications."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class MemoryScope(StrEnum):
    THREAD = "thread"
    AGENT = "agent"
    USER = "user"


@dataclass(frozen=True, slots=True)
class MemorySubject:
    """A host-resolved namespace, never a model-supplied native filter."""

    scope: MemoryScope
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope) or not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("Memory subjects require a scope and a nonempty trusted identifier")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    text: str
    subjects: tuple[MemorySubject, ...]
    score: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Memory records require an identifier and nonempty text")
        self.text.encode("utf-8")
        if not self.subjects or any(not isinstance(item, MemorySubject) for item in self.subjects):
            raise ValueError("Memory records require trusted subjects")
        if self.score is not None and (
            isinstance(self.score, bool) or not isinstance(self.score, int | float) or not isfinite(self.score)
        ):
            raise ValueError("Memory scores must be finite")


@dataclass(frozen=True, slots=True)
class MemoryPagination:
    """Native continuation; None is terminal only when this object is present."""

    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryPage:
    items: tuple[MemoryRecord, ...]
    # Absence means bounded results with unknown completeness, not a final page.
    pagination: MemoryPagination | None = None


class MemoryRecordNotFound(LookupError):
    """No record exists within the requested subject."""


class MemoryPaginationUnsupported(ValueError):
    """This backend cannot traverse records with cursors."""


class MemoryWriteUnconfirmed(RuntimeError):
    """A write may have committed. Inspect before repeating; never automatically retry."""


def validate_memory_text(text: str) -> None:
    if not isinstance(text, str) or not text.strip() or not 1 <= len(text) <= 8000:
        raise ValueError("Memory text must contain 1 to 8000 characters and cannot be blank")
    text.encode("utf-8")


def require_memory_subject(record: MemoryRecord, subjects: tuple[MemorySubject, ...]) -> None:
    if not any(subject in record.subjects for subject in subjects):
        raise MemoryRecordNotFound(record.id)


class MemoryBackend(ABC):
    """Borrowed asynchronous storage boundary; the host owns lifetime and deadlines.

    All operations are subject-scoped. Implementations verify returned subjects,
    preserve explicit text without inference, and confirm writes by readback.
    No method retries writes. Cancellation propagates without implying rollback.
    """

    @abstractmethod
    async def search(
        self, query: str, *, subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None = None
    ) -> tuple[MemoryRecord, ...]: ...

    @abstractmethod
    async def list(self, subject: MemorySubject, *, limit: int, cursor: str | None = None) -> MemoryPage: ...

    @abstractmethod
    async def add(self, text: str, *, subject: MemorySubject) -> MemoryRecord: ...

    @abstractmethod
    async def get(self, memory_id: str, *, subject: MemorySubject) -> MemoryRecord: ...

    @abstractmethod
    async def update(self, memory_id: str, text: str, *, subject: MemorySubject) -> MemoryRecord: ...

    @abstractmethod
    async def delete(self, memory_id: str, *, subject: MemorySubject) -> None: ...
