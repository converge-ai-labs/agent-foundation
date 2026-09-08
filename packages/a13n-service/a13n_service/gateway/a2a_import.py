"""Bounded A2A raw and URL Part import into immutable Asset candidates."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from urllib.parse import urljoin

import anyio
import httpx2
from a2a.types import a2a_pb2 as a2a
from google.protobuf.json_format import MessageToDict
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.assets import Asset
from a13n_service.assets.errors import AssetError
from a13n_service.assets.uploads import AssetUploadService, PreparedAssetPublication
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam import AuthenticatedActor
from a13n_service.interactions.input import AgentInput

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class A2APartImportError(ValueError):
    def __init__(self, code: str, message: str, *, category: ErrorCategory = ErrorCategory.invalid_input) -> None:
        super().__init__(message)
        self.code = code
        self.category = category


@dataclass(frozen=True, slots=True)
class PreparedA2AMessage:
    input: AgentInput
    publications: tuple[PreparedAssetPublication, ...]

    @property
    def prepared_assets(self) -> dict[str, Asset]:
        return {publication.asset.id: publication.asset for publication in self.publications}


class A2APartImporter:
    """Acquire untrusted A2A Part content without holding a database transaction."""

    def __init__(
        self,
        assets: AssetUploadService,
        http_client: httpx2.AsyncClient | None,
        endpoint_policy: EndpointPolicy,
        *,
        max_redirects: int,
        timeout_seconds: float,
    ) -> None:
        if max_redirects < 0 or timeout_seconds <= 0:
            raise ValueError("A2A Part import bounds are invalid")
        self._assets = assets
        self._http = http_client
        self._endpoint_policy = endpoint_policy
        self._max_redirects = max_redirects
        self._timeout_seconds = timeout_seconds

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        message: a2a.Message,
    ) -> PreparedA2AMessage:
        content: list[dict[str, object]] = []
        data: list[object] = []
        publications: list[PreparedAssetPublication] = []
        try:
            for index, part in enumerate(message.parts):
                kind = part.WhichOneof("content")
                if kind == "text":
                    if not part.text:
                        raise A2APartImportError(
                            "message_invalid", "Text Parts cannot be empty.", category=ErrorCategory.invalid_request
                        )
                    content.append({"type": "text", "text": part.text})
                elif kind == "data":
                    data.append(MessageToDict(part)["data"])
                elif kind == "raw":
                    publication = await self._prepare_raw(actor=actor, part=part, index=index)
                    publications.append(publication)
                    content.append(_binary_block(publication))
                elif kind == "url":
                    publication = await self._prepare_url(actor=actor, part=part, index=index)
                    publications.append(publication)
                    content.append(_binary_block(publication))
                else:
                    raise A2APartImportError(
                        "message_invalid",
                        "The Message Part content is missing.",
                        category=ErrorCategory.invalid_request,
                    )
            structured = None if not data else data[0] if len(data) == 1 else data
            return PreparedA2AMessage(
                input=AgentInput.model_validate(
                    {
                        "schema_version": "2",
                        "content": content,
                        "structured_content": structured,
                    }
                ),
                publications=tuple(publications),
            )
        except BaseException:
            for publication in publications:
                await self._assets.discard_protocol_import(publication)
            raise

    async def commit_in_transaction(
        self,
        database: AsyncSession,
        *,
        actor: AuthenticatedActor,
        prepared: PreparedA2AMessage,
    ) -> None:
        await self._assets.commit_protocol_imports_in_transaction(
            database,
            actor=actor,
            publications=prepared.publications,
        )

    async def discard(self, prepared: PreparedA2AMessage) -> None:
        for publication in prepared.publications:
            await self._assets.discard_protocol_import(publication)

    async def _prepare_raw(
        self,
        *,
        actor: AuthenticatedActor,
        part: a2a.Part,
        index: int,
    ) -> PreparedAssetPublication:
        async def body():
            yield bytes(part.raw)

        return await self._prepare_asset(
            actor=actor,
            part=part,
            index=index,
            body=body(),
            content_length=len(part.raw),
        )

    async def _prepare_url(
        self,
        *,
        actor: AuthenticatedActor,
        part: a2a.Part,
        index: int,
    ) -> PreparedAssetPublication:
        if not part.url:
            raise A2APartImportError(
                "message_invalid", "URL Parts cannot be empty.", category=ErrorCategory.invalid_request
            )
        if self._http is None:
            raise A2APartImportError("part_url_fetch_failed", "The URL Part importer is unavailable.")
        current = part.url
        try:
            with anyio.fail_after(self._timeout_seconds):
                for redirect_count in range(self._max_redirects + 1):
                    current = await self._endpoint_policy.validate(current, resolve_dns=True)
                    async with self._http.stream(
                        "GET",
                        current,
                        headers={"Accept": "*/*"},
                        follow_redirects=False,
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            if redirect_count == self._max_redirects:
                                raise A2APartImportError(
                                    "part_url_redirect_limit", "The URL Part redirected too many times."
                                )
                            location = response.headers.get("location")
                            if location is None:
                                raise A2APartImportError("part_url_invalid", "The URL Part redirect is invalid.")
                            current, _same_origin = await self._endpoint_policy.validate_redirect(
                                current,
                                urljoin(current, location),
                                resolve_dns=True,
                            )
                            continue
                        if response.status_code < 200 or response.status_code >= 300:
                            raise A2APartImportError(
                                "part_url_fetch_failed",
                                "The URL Part could not be fetched.",
                            )
                        content_length = _content_length(response.headers.get("content-length"))
                        return await self._prepare_asset(
                            actor=actor,
                            part=part,
                            index=index,
                            body=response.aiter_bytes(),
                            content_length=content_length,
                        )
        except TimeoutError as error:
            raise A2APartImportError("part_url_timeout", "The URL Part fetch timed out.") from error
        except EndpointPolicyError as error:
            raise A2APartImportError("part_url_invalid", "The URL Part destination is not permitted.") from error
        except httpx2.HTTPError as error:
            raise A2APartImportError("part_url_fetch_failed", "The URL Part could not be fetched.") from error
        raise A2APartImportError("part_url_redirect_limit", "The URL Part redirected too many times.")

    async def _prepare_asset(
        self,
        *,
        actor: AuthenticatedActor,
        part: a2a.Part,
        index: int,
        body: AsyncIterable[bytes],
        content_length: int | None,
    ) -> PreparedAssetPublication:
        try:
            return await self._assets.prepare_protocol_import(
                actor=actor,
                workspace_id=actor.workspace_id,
                filename=part.filename or f"part-{index}",
                media_type=part.media_type or None,
                body=body,
                content_length=content_length,
            )
        except AssetError as error:
            raise A2APartImportError(
                "part_content_invalid",
                "The Part content could not be imported.",
                category=error.category,
            ) from error


def _binary_block(publication: PreparedAssetPublication) -> dict[str, object]:
    return {
        "type": "binary",
        "source": {"type": "asset", "asset_id": publication.asset.id},
        "delivery": "auto",
    }


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise A2APartImportError("part_url_invalid", "The URL Part Content-Length is invalid.") from error
    if parsed < 0:
        raise A2APartImportError("part_url_invalid", "The URL Part Content-Length is invalid.")
    return parsed


__all__ = ["A2APartImportError", "A2APartImporter", "PreparedA2AMessage"]
