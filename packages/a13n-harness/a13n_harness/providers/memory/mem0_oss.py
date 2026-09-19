"""Mem0 OSS HTTP storage and its reusable definition."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx2
from pydantic import JsonValue

from .cleanup import bounded_cleanup
from .configuration import Mem0Credential, Mem0OSSConfiguration
from .contracts import (
    MemoryPage,
    MemoryPaginationUnsupported,
    MemoryRecord,
    MemoryRecordNotFound,
    MemorySubject,
)
from .definition import MemoryProviderDefinition
from .mem0_common import (
    Mem0Backend,
    document_filters,
    document_records,
    parse_records,
    subject_filter,
    validate_list_limit,
    validate_search_options,
)


class Mem0OSSBackend(Mem0Backend):
    """Borrow an HTTP client for the unmodified public OSS server API."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def search(
        self, query: str, *, subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None = None
    ) -> tuple[MemoryRecord, ...]:
        validate_search_options(subjects, limit, threshold)

        async def search_one(subject: MemorySubject) -> tuple[MemoryRecord, ...]:
            body: dict[str, Any] = {"query": query, "filters": subject_filter(subject), "top_k": limit}
            if threshold is not None:
                body["threshold"] = threshold
            response = await self.client.post("search", json=body)
            response.raise_for_status()
            return parse_records(response.json(), (subject,), limit)

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

    async def search_documents(
        self, query: str, *, subject: MemorySubject, record_keys: tuple[str, ...], limit: int
    ) -> tuple[MemoryRecord, ...]:
        validate_search_options((subject,), limit, None)
        if not record_keys:
            return ()
        filters = document_filters(subject, record_keys)
        response = await self.client.post("search", json={"query": query, "filters": filters, "top_k": limit})
        response.raise_for_status()
        return document_records(response.json(), subject, record_keys, limit)

    async def list(self, subject: MemorySubject, *, limit: int, cursor: str | None = None) -> MemoryPage:
        validate_list_limit(limit)
        if cursor is not None:
            raise MemoryPaginationUnsupported("The OSS memory API does not support pagination")
        response = await self.client.get("memories", params={**subject_filter(subject), "top_k": limit})
        response.raise_for_status()
        return MemoryPage(parse_records(response.json(), (subject,), limit))

    async def _add(self, text: str, subject: MemorySubject, metadata: Mapping[str, JsonValue] | None = None) -> object:
        response = await self.client.post(
            "memories",
            json={
                "messages": [{"role": "user", "content": text}],
                **subject_filter(subject),
                "infer": False,
                **({"metadata": dict(metadata)} if metadata is not None else {}),
            },
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
async def open_mem0_oss(
    configuration: Mem0OSSConfiguration, credential: Mem0Credential | None, *, timeout: float = 30
) -> AsyncIterator[Mem0OSSBackend]:
    """Open a host-owned transport; secrets never enter Capability serialization."""
    if credential is None:
        raise ValueError("Mem0 OSS requires an API key credential")
    client = httpx2.AsyncClient(
        base_url=configuration.base_url.rstrip("/") + "/",
        headers={"X-API-Key": credential.api_key.get_secret_value()},
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        yield Mem0OSSBackend(client)
    finally:
        with bounded_cleanup():
            await client.aclose()


DEFINITION = MemoryProviderDefinition(
    type="mem0_oss",
    display_name="Mem0 OSS",
    configuration_model=Mem0OSSConfiguration,
    credential_model=Mem0Credential,
    open_backend=open_mem0_oss,
    supports_documents=True,
    setup_url="https://docs.mem0.ai/open-source/overview",
)
