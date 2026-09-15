"""Native Mem0 adapters implementing the provider-neutral memory contract."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx2
from mem0 import AsyncMemoryClient
from mem0.exceptions import MemoryNotFoundError

from a13n_harness.memory import (
    MemoryBackend,
    MemoryPage,
    MemoryPagination,
    MemoryPaginationUnsupported,
    MemoryRecord,
    MemoryRecordNotFound,
    MemoryScope,
    MemorySubject,
    MemoryWriteUnconfirmed,
    require_memory_subject,
    validate_memory_text,
)

_FIELDS = {MemoryScope.THREAD: "run_id", MemoryScope.AGENT: "agent_id", MemoryScope.USER: "user_id"}


def _filter(subject: MemorySubject) -> dict[str, str]:
    return {_FIELDS[subject.scope]: subject.value}


def _record(raw: object) -> MemoryRecord:
    if not isinstance(raw, Mapping):
        raise ValueError("Invalid Mem0 record")
    memory_id, text = raw.get("id"), raw.get("memory")
    if not isinstance(memory_id, str) or not isinstance(text, str):
        raise ValueError("Invalid Mem0 identifier or text")
    return MemoryRecord(
        id=memory_id,
        text=text,
        subjects=tuple(
            MemorySubject(scope, raw[field]) for scope, field in _FIELDS.items() if raw.get(field) is not None
        ),
        score=raw.get("score"),
    )


def _records(raw: object, subjects: tuple[MemorySubject, ...], limit: int) -> tuple[MemoryRecord, ...]:
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


def _search_options(subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None) -> None:
    if not 1 <= len(subjects) <= 3 or len(set(subjects)) != len(subjects):
        raise ValueError("Memory search requires one to three distinct trusted subjects")
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Search limit must be between 1 and 100")
    if threshold is not None and (isinstance(threshold, bool) or not 0 <= threshold <= 1):
        raise ValueError("Search threshold must be between 0 and 1")


def _list_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("List limit must be between 1 and 1000")


class _Mem0Backend(MemoryBackend):
    """Shared confirmation rules, with native transport details private to adapters."""

    @abstractmethod
    async def _add(self, text: str, subject: MemorySubject) -> object: ...

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


class Mem0PlatformBackend(_Mem0Backend):
    """Borrow the native Platform SDK client; never emulate it with an OSS client."""

    def __init__(self, client: AsyncMemoryClient) -> None:
        self.client = client

    async def search(
        self, query: str, *, subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None = None
    ) -> tuple[MemoryRecord, ...]:
        _search_options(subjects, limit, threshold)
        filters = _filter(subjects[0]) if len(subjects) == 1 else {"OR": [_filter(item) for item in subjects]}
        options: dict[str, Any] = {"filters": filters, "top_k": limit}
        if threshold is not None:
            options["threshold"] = threshold
        return _records(await self.client.search(query, **options), subjects, limit)

    async def list(self, subject: MemorySubject, *, limit: int, cursor: str | None = None) -> MemoryPage:
        _list_limit(limit)
        page = 1
        if cursor is not None:
            if not cursor.isascii() or not cursor.isdecimal() or not 1 <= int(cursor) <= 1_000_000:
                raise ValueError("Invalid Platform memory cursor")
            page = int(cursor)
        response = await self.client.get_all(filters=_filter(subject), page=page, page_size=min(limit, 200))
        records = _records(response, (subject,), min(limit, 200))
        if not isinstance(response, Mapping):
            raise ValueError("Invalid Platform memory page")
        # Never follow remote URLs (which may contain secrets or target another host).
        return MemoryPage(records, MemoryPagination(str(page + 1) if response.get("next") else None))

    async def _add(self, text: str, subject: MemorySubject) -> object:
        return await self.client.add(text, filters=_filter(subject), infer=False)

    async def _get(self, memory_id: str) -> object:
        try:
            return await self.client.get(memory_id)
        except MemoryNotFoundError as error:
            raise MemoryRecordNotFound(memory_id) from error

    async def _update(self, memory_id: str, text: str) -> None:
        await self.client.update(memory_id, text=text)

    async def _delete(self, memory_id: str) -> None:
        await self.client.delete(memory_id)


class Mem0OSSBackend(_Mem0Backend):
    """Borrow an HTTP client for the unmodified public OSS server API."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def search(
        self, query: str, *, subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None = None
    ) -> tuple[MemoryRecord, ...]:
        _search_options(subjects, limit, threshold)

        async def search_one(subject: MemorySubject) -> tuple[MemoryRecord, ...]:
            body: dict[str, Any] = {"query": query, "filters": _filter(subject), "top_k": limit}
            if threshold is not None:
                body["threshold"] = threshold
            response = await self.client.post("search", json=body)
            response.raise_for_status()
            return _records(response.json(), (subject,), limit)

        # One caller deadline covers all scopes; failure cancels siblings, never partial recall.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(search_one(subject)) for subject in subjects]
        if len(tasks) == 1:
            return tasks[0].result()
        records: dict[str, MemoryRecord] = {}
        for task in tasks:
            for item in task.result():
                if item.score is None:
                    raise ValueError("OSS union results require scores")
                existing = records.get(item.id)
                if existing is None or existing.score is None or item.score > existing.score:
                    records[item.id] = item
        return tuple(sorted(records.values(), key=lambda item: (-(item.score or 0), item.id))[:limit])

    async def list(self, subject: MemorySubject, *, limit: int, cursor: str | None = None) -> MemoryPage:
        _list_limit(limit)
        if cursor is not None:
            raise MemoryPaginationUnsupported("The OSS memory API does not support pagination")
        response = await self.client.get("memories", params={**_filter(subject), "top_k": limit})
        response.raise_for_status()
        return MemoryPage(_records(response.json(), (subject,), limit))

    async def _add(self, text: str, subject: MemorySubject) -> object:
        response = await self.client.post(
            "memories",
            json={"messages": [{"role": "user", "content": text}], **_filter(subject), "infer": False},
        )
        response.raise_for_status()
        return response.json()

    async def _get(self, memory_id: str) -> object:
        response = await self.client.get(f"memories/{quote(memory_id, safe='')}")
        if response.status_code == 404:
            raise MemoryRecordNotFound(memory_id)
        response.raise_for_status()
        result = response.json()
        if result is None:
            raise MemoryRecordNotFound(memory_id)
        return result

    async def _update(self, memory_id: str, text: str) -> None:
        response = await self.client.put(f"memories/{quote(memory_id, safe='')}", json={"text": text})
        response.raise_for_status()

    async def _delete(self, memory_id: str) -> None:
        response = await self.client.delete(f"memories/{quote(memory_id, safe='')}")
        response.raise_for_status()


@asynccontextmanager
async def open_mem0_oss(*, base_url: str, api_key: str, timeout: float = 30) -> AsyncIterator[Mem0OSSBackend]:
    """Open a host-owned transport; secrets never enter Capability serialization."""
    async with httpx2.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        headers={"X-API-Key": api_key},
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        yield Mem0OSSBackend(client)


class _DeferredValidationClient(AsyncMemoryClient):
    """Keep SDK network calls asynchronous, including initial validation."""

    def __init__(self, *, api_key: str, host: str | None) -> None:
        super().__init__(api_key=api_key, host=host)
        self.org_id = None
        self.project_id = None

    def _validate_api_key(self) -> None:
        # Native construction otherwise performs an unbounded synchronous ping.
        self.org_id = "deferred"
        self.project_id = "deferred"


@asynccontextmanager
async def open_mem0_platform(*, api_key: str, base_url: str | None = None) -> AsyncIterator[Mem0PlatformBackend]:
    """Open the native SDK with local-only construction and one host lifetime."""
    async with _DeferredValidationClient(api_key=api_key, host=base_url) as client:
        yield Mem0PlatformBackend(client)
