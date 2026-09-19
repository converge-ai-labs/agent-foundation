"""Mem0 subject mapping, record validation and write confirmation."""

from abc import abstractmethod
from collections.abc import Mapping

from pydantic import JsonValue

from .contracts import (
    MemoryDocumentBackend,
    MemoryDocumentScope,
    MemoryRecord,
    MemoryRecordNotFound,
    MemoryScope,
    MemorySubject,
    MemoryWriteUnconfirmed,
    require_memory_subject,
    validate_memory_text,
)

_FIELDS = {MemoryScope.THREAD: "run_id", MemoryScope.AGENT: "agent_id", MemoryScope.USER: "user_id"}


def subject_filter(subject: MemorySubject) -> dict[str, str]:
    return {"run_id" if subject.scope is MemoryDocumentScope.CONVERSATION else _FIELDS[subject.scope]: subject.value}


def _record(raw: object) -> MemoryRecord:
    if not isinstance(raw, Mapping):
        raise ValueError("Invalid Mem0 record")
    memory_id, text = raw.get("id"), raw.get("memory")
    if not isinstance(memory_id, str) or not isinstance(text, str):
        raise ValueError("Invalid Mem0 identifier or text")
    metadata = raw.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise ValueError("Invalid Mem0 metadata")
    conversation = metadata.get("a13n_scope") == "conversation"
    return MemoryRecord(
        id=memory_id,
        text=text,
        subjects=tuple(
            MemorySubject(
                MemoryDocumentScope.CONVERSATION if scope is MemoryScope.THREAD and conversation else scope, raw[field]
            )
            for scope, field in _FIELDS.items()
            if raw.get(field) is not None
        ),
        score=raw.get("score"),
        metadata=dict(metadata),
    )


def parse_records(raw: object, subjects: tuple[MemorySubject, ...], limit: int) -> tuple[MemoryRecord, ...]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("results"), list) or len(raw["results"]) > limit:
        raise ValueError("Invalid Mem0 results")
    records = tuple(_record(item) for item in raw["results"])
    for record in records:
        require_memory_subject(record, subjects)
    return records


def _added_id(response: object) -> str:
    if not isinstance(response, Mapping):
        raise ValueError("Invalid memory add response")
    results = response.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise ValueError("Memory write was not confirmed")
    item = results[0]
    if not isinstance(item, Mapping) or item.get("event") != "ADD":
        raise ValueError("Memory write was not confirmed")
    memory_id = item.get("id")
    if not isinstance(memory_id, str) or not memory_id:
        raise ValueError("Memory write was not confirmed")
    return memory_id


def validate_search_options(subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None) -> None:
    if not 1 <= len(subjects) <= 3 or len(set(subjects)) != len(subjects):
        raise ValueError("Memory search requires one to three distinct trusted subjects")
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Search limit must be between 1 and 100")
    if threshold is not None and (isinstance(threshold, bool) or not 0 <= threshold <= 1):
        raise ValueError("Search threshold must be between 0 and 1")


def validate_list_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("List limit must be between 1 and 1000")


class Mem0Backend(MemoryDocumentBackend):
    """Shared confirmation rules, with native transport details private to adapters."""

    @abstractmethod
    async def _add(
        self, text: str, subject: MemorySubject, metadata: Mapping[str, JsonValue] | None = None
    ) -> object: ...

    @abstractmethod
    async def _get(self, memory_id: str) -> object: ...

    @abstractmethod
    async def _update(self, memory_id: str, text: str) -> None: ...

    @abstractmethod
    async def _delete(self, memory_id: str) -> None: ...

    async def get(self, memory_id: str, *, subject: MemorySubject) -> MemoryRecord:
        result = _record(await self._get(memory_id))
        if result.id != memory_id:
            raise ValueError("Memory identifier mismatch")
        require_memory_subject(result, (subject,))
        return result

    async def add(self, text: str, *, subject: MemorySubject) -> MemoryRecord:
        validate_memory_text(text)
        try:
            memory_id = _added_id(await self._add(text, subject))
            record = await self.get(memory_id, subject=subject)
            if record.text != text:
                raise ValueError("Explicit memory text was not persisted")
            return record
        except Exception as error:
            raise MemoryWriteUnconfirmed("Inspect current memory before repeating the write") from error

    async def add_document(
        self, text: str, *, subject: MemorySubject, metadata: Mapping[str, JsonValue]
    ) -> MemoryRecord:
        validate_memory_text(text)
        if subject.scope is not MemoryDocumentScope.CONVERSATION:
            raise ValueError("Document storage requires a conversation subject")
        expected = dict(metadata)
        expected["a13n_scope"] = "conversation"
        try:
            memory_id = _added_id(await self._add(text, subject, expected))
            record = await self.get(memory_id, subject=subject)
            if record.text != text or dict(record.metadata) != expected:
                raise ValueError("Document content or metadata was not persisted exactly")
            return record
        except Exception as error:
            raise MemoryWriteUnconfirmed("Inspect current memory before repeating the write") from error

    async def update(self, memory_id: str, text: str, *, subject: MemorySubject) -> MemoryRecord:
        validate_memory_text(text)
        await self.get(memory_id, subject=subject)
        try:
            await self._update(memory_id, text)
            record = await self.get(memory_id, subject=subject)
            if record.text != text:
                raise ValueError("Memory update was not confirmed")
            return record
        except Exception as error:
            raise MemoryWriteUnconfirmed("Inspect current memory before repeating the write") from error

    async def delete(self, memory_id: str, *, subject: MemorySubject) -> None:
        await self.get(memory_id, subject=subject)
        try:
            await self._delete(memory_id)
            try:
                await self._get(memory_id)
            except MemoryRecordNotFound:
                return
            raise ValueError("Memory deletion was not confirmed")
        except Exception as error:
            raise MemoryWriteUnconfirmed("Inspect current memory before repeating the write") from error


def document_filters(subject: MemorySubject, record_keys: tuple[str, ...]) -> dict[str, object]:
    if len(record_keys) > 1000 or any(not key or len(key) > 128 for key in record_keys):
        raise ValueError("Document search key budget exceeded")
    return {**subject_filter(subject), "record_key": {"in": list(record_keys)}}


def document_records(
    raw: object, subject: MemorySubject, record_keys: tuple[str, ...], limit: int
) -> tuple[MemoryRecord, ...]:
    records = parse_records(raw, (subject,), limit)
    if any(record.metadata.get("record_key") not in record_keys for record in records):
        raise ValueError("Provider returned a document outside the authorized key set")
    return records
