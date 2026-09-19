"""Native Mem0 Platform storage; SDK imports occur only on use."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from .cleanup import bounded_cleanup
from .configuration import Mem0Credential, Mem0PlatformConfiguration
from .contracts import (
    MemoryPage,
    MemoryPagination,
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


class Mem0PlatformBackend(Mem0Backend):
    """Borrow the native Platform SDK client; never emulate it with an OSS client."""

    def __init__(self, client: AsyncMemoryClient) -> None:
        self.client = client

    async def search(
        self, query: str, *, subjects: tuple[MemorySubject, ...], limit: int, threshold: float | None = None
    ) -> tuple[MemoryRecord, ...]:
        validate_search_options(subjects, limit, threshold)
        filters = (
            subject_filter(subjects[0]) if len(subjects) == 1 else {"OR": [subject_filter(item) for item in subjects]}
        )
        options: dict[str, Any] = {"filters": filters, "top_k": limit}
        if threshold is not None:
            options["threshold"] = threshold
        return parse_records(await self.client.search(query, **options), subjects, limit)

    async def search_documents(
        self, query: str, *, subject: MemorySubject, record_keys: tuple[str, ...], limit: int
    ) -> tuple[MemoryRecord, ...]:
        validate_search_options((subject,), limit, None)
        if not record_keys:
            return ()
        filters = document_filters(subject, record_keys)
        return document_records(
            await self.client.search(query, filters=filters, top_k=limit), subject, record_keys, limit
        )

    async def list(self, subject: MemorySubject, *, limit: int, cursor: str | None = None) -> MemoryPage:
        validate_list_limit(limit)
        page = 1
        if cursor is not None:
            if not cursor.isascii() or not cursor.isdecimal() or not 1 <= int(cursor) <= 1_000_000:
                raise ValueError("Invalid Platform memory cursor")
            page = int(cursor)
        response = await self.client.get_all(filters=subject_filter(subject), page=page, page_size=min(limit, 200))
        records = parse_records(response, (subject,), min(limit, 200))
        if not isinstance(response, Mapping):
            raise ValueError("Invalid Platform memory page")
        # Never follow remote URLs (which may contain secrets or target another host).
        return MemoryPage(records, MemoryPagination(str(page + 1) if response.get("next") else None))

    async def _add(self, text: str, subject: MemorySubject, metadata: Mapping[str, JsonValue] | None = None) -> object:
        options: dict[str, Any] = {"filters": subject_filter(subject), "infer": False}
        if metadata is not None:
            options["metadata"] = dict(metadata)
        return await self.client.add(text, **options)

    async def _get(self, memory_id: str) -> object:
        from mem0.exceptions import MemoryNotFoundError

        try:
            return await self.client.get(memory_id)
        except MemoryNotFoundError as error:
            raise MemoryRecordNotFound(memory_id) from error

    async def _update(self, memory_id: str, text: str) -> None:
        await self.client.update(memory_id, text=text)

    async def _delete(self, memory_id: str) -> None:
        await self.client.delete(memory_id)


@asynccontextmanager
async def open_mem0_platform(
    configuration: Mem0PlatformConfiguration, credential: Mem0Credential | None
) -> AsyncIterator[Mem0PlatformBackend]:
    """Open the native SDK with local-only construction and one host lifetime."""
    if credential is None:
        raise ValueError("Mem0 Platform requires an API key credential")
    try:
        from mem0 import AsyncMemoryClient
    except ImportError as error:
        raise ImportError("Mem0 Platform requires a13n-harness[mem0]") from error

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

    client = _DeferredValidationClient(api_key=credential.api_key.get_secret_value(), host=configuration.base_url)
    try:
        yield Mem0PlatformBackend(client)
    finally:
        with bounded_cleanup():
            await client.async_client.aclose()


if TYPE_CHECKING:
    from mem0 import AsyncMemoryClient


DEFINITION = MemoryProviderDefinition(
    type="mem0_platform",
    display_name="Mem0 Platform",
    configuration_model=Mem0PlatformConfiguration,
    credential_model=Mem0Credential,
    open_backend=open_mem0_platform,
    supports_documents=True,
    setup_url="https://app.mem0.ai/dashboard/api-keys",
)
