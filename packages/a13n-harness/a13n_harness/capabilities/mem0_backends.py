"""Native Mem0 transports. Clients and their lifetime belong to the embedding host."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal
from urllib.parse import quote

import httpx2
from mem0 import AsyncMemoryClient
from mem0.exceptions import MemoryNotFoundError


class Mem0RecordNotFound(LookupError):
    """The selected native backend has no memory with this identifier."""


@dataclass(frozen=True, slots=True)
class Mem0Subject:
    """One trusted, provider-visible subject, not a model-supplied filter."""

    field: Literal["run_id", "agent_id", "user_id"]
    value: str

    def __post_init__(self) -> None:
        if self.field not in {"run_id", "agent_id", "user_id"} or not self.value.strip():
            raise ValueError("A Mem0 subject requires a supported field and nonempty value")

    def filter(self) -> dict[str, str]:
        return {self.field: self.value}


class Mem0Backend(ABC):
    """Small transport boundary shared by recall and explicit memory tools.

    Callers own deadlines and authorization. Implementations must not retry writes.
    Management operations remain on native clients, outside the model tool surface.
    """

    @abstractmethod
    async def search(
        self, query: str, *, subjects: tuple[Mem0Subject, ...], limit: int, threshold: float | None = None
    ) -> object: ...

    @abstractmethod
    async def list(self, subject: Mem0Subject, *, limit: int, cursor: str | None = None) -> object: ...

    @abstractmethod
    async def add(self, text: str, *, subject: Mem0Subject) -> object: ...


def added_memory_id(response: object) -> str:
    """Require a completed explicit add, not an accepted/queued operation."""
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


def _subjects(subjects: tuple[Mem0Subject, ...]) -> None:
    if not 1 <= len(subjects) <= 3 or len(set(subjects)) != len(subjects):
        raise ValueError("Mem0 search requires one to three distinct trusted subjects")


class Mem0PlatformBackend(Mem0Backend):
    """Borrow a native Platform SDK client; never emulate it with an OSS client."""

    def __init__(self, client: AsyncMemoryClient) -> None:
        self.client = client

    async def search(
        self, query: str, *, subjects: tuple[Mem0Subject, ...], limit: int, threshold: float | None = None
    ) -> object:
        _subjects(subjects)
        filters = subjects[0].filter() if len(subjects) == 1 else {"OR": [item.filter() for item in subjects]}
        options: dict[str, Any] = {"filters": filters, "top_k": limit}
        if threshold is not None:
            options["threshold"] = threshold
        return await self.client.search(query, **options)

    async def list(self, subject: Mem0Subject, *, limit: int, cursor: str | None = None) -> object:
        page = 1
        if cursor is not None:
            if not cursor.isascii() or not cursor.isdecimal() or not 1 <= int(cursor) <= 1_000_000:
                raise ValueError("Invalid Platform memory cursor")
            page = int(cursor)
        response = await self.client.get_all(filters=subject.filter(), page=page, page_size=limit)
        if not isinstance(response, Mapping):
            raise ValueError("Invalid Platform memory page")
        # Do not follow backend URLs (which may contain credentials or a different host).
        return {**response, "next_cursor": str(page + 1) if response.get("next") else None}

    async def add(self, text: str, *, subject: Mem0Subject) -> object:
        return await self.client.add(text, filters=subject.filter(), infer=False)

    async def get(self, memory_id: str) -> object:
        try:
            return await self.client.get(memory_id)
        except MemoryNotFoundError as error:
            raise Mem0RecordNotFound(memory_id) from error

    async def update(self, memory_id: str, text: str) -> object:
        return await self.client.update(memory_id, text=text)

    async def delete(self, memory_id: str) -> object:
        return await self.client.delete(memory_id)


class Mem0OSSBackend(Mem0Backend):
    """Borrow an HTTP client configured for the native OSS server and its API key.

    Listing uses the pinned OSS PGVector keyset-pagination extension.
    """

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def search(
        self, query: str, *, subjects: tuple[Mem0Subject, ...], limit: int, threshold: float | None = None
    ) -> object:
        _subjects(subjects)

        async def search_one(subject: Mem0Subject) -> object:
            body: dict[str, Any] = {"query": query, "filters": subject.filter(), "top_k": limit}
            if threshold is not None:
                body["threshold"] = threshold
            response = await self.client.post("search", json=body)
            response.raise_for_status()
            return response.json()

        # TaskGroup cancels siblings on failure; the caller's one deadline covers
        # all requests. A failed scope must not become a successful partial recall.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(search_one(subject)) for subject in subjects]
        if len(tasks) == 1:
            return tasks[0].result()
        records: dict[str, dict[str, Any]] = {}
        for task in tasks:
            response = task.result()
            if not isinstance(response, Mapping) or not isinstance(response.get("results"), list):
                raise ValueError("Invalid OSS memory search response")
            for item in response["results"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise ValueError("OSS union results require memory identifiers")
                score = item.get("score")
                if isinstance(score, bool) or not isinstance(score, int | float) or not isfinite(score):
                    raise ValueError("OSS union results require finite scores")
                existing = records.get(item["id"])
                if existing is None or score > existing["score"]:
                    records[item["id"]] = item
        return {"results": sorted(records.values(), key=lambda item: (-item["score"], item["id"]))[:limit]}

    async def list(self, subject: Mem0Subject, *, limit: int, cursor: str | None = None) -> object:
        params: dict[str, str | int] = {**subject.filter(), "top_k": limit}
        if cursor is not None:
            params["cursor"] = cursor
        response = await self.client.get("memories/page", params=params)
        response.raise_for_status()
        return response.json()

    async def add(self, text: str, *, subject: Mem0Subject) -> object:
        response = await self.client.post(
            "memories",
            json={"messages": [{"role": "user", "content": text}], **subject.filter(), "infer": False},
        )
        response.raise_for_status()
        return response.json()

    async def get(self, memory_id: str) -> object:
        response = await self.client.get(f"memories/{quote(memory_id, safe='')}")
        if response.status_code == 404:
            raise Mem0RecordNotFound(memory_id)
        response.raise_for_status()
        result = response.json()
        if result is None:
            raise Mem0RecordNotFound(memory_id)
        return result

    async def update(self, memory_id: str, text: str) -> object:
        response = await self.client.put(f"memories/{quote(memory_id, safe='')}", json={"text": text})
        response.raise_for_status()
        return response.json()

    async def delete(self, memory_id: str) -> object:
        response = await self.client.delete(f"memories/{quote(memory_id, safe='')}")
        response.raise_for_status()
        return response.json()


@asynccontextmanager
async def open_mem0_oss(*, base_url: str, api_key: str, timeout: float = 30) -> AsyncIterator[Mem0OSSBackend]:
    """Open a host-owned native OSS transport; secrets are never serialized by the Capability."""
    async with httpx2.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        headers={"X-API-Key": api_key},
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        yield Mem0OSSBackend(client)


class _DeferredValidationClient(AsyncMemoryClient):
    """Keep the SDK's network calls asynchronous, including initial validation."""

    def __init__(self, *, api_key: str, host: str | None) -> None:
        super().__init__(api_key=api_key, host=host)
        self.org_id = None
        self.project_id = None

    def _validate_api_key(self) -> None:
        # Native construction otherwise performs an unbounded synchronous ping.
        # These temporary values satisfy its unused project helper; normal async
        # memory operations validate the real credentials within the host deadline.
        self.org_id = "deferred"
        self.project_id = "deferred"


@asynccontextmanager
async def open_mem0_platform(*, api_key: str, base_url: str | None = None) -> AsyncIterator[Mem0PlatformBackend]:
    """Open the native Platform SDK with local-only construction and one host lifetime."""
    async with _DeferredValidationClient(api_key=api_key, host=base_url) as client:
        yield Mem0PlatformBackend(client)
