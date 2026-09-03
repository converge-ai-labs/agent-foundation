"""Versioned Agent input acceptance and Harness-native materialization."""

from __future__ import annotations

import json
import posixpath
from asyncio import gather
from collections.abc import AsyncIterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol

import rfc8785
from a13n_harness import HarnessModelCharacteristics, ModelCapability, RunInputValue
from jsonschema import Draft202012Validator
from pydantic import (
    AfterValidator,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_ai import AudioUrl, DocumentUrl, ImageUrl, VideoUrl
from pydantic_ai import BinaryContent as NativeBinaryContent
from pydantic_ai.messages import UserContent

from a13n_service.agents.domain import InputAdapterConfig
from a13n_service.assets.domain import Asset, normalize_asset_filename, normalize_media_type
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError

from .domain import BoundedKey, JsonObject, ObjectId, StrictModel

_MAX_CONTENT_BLOCKS = 256
_MAX_TEXT_LENGTH = 1_048_576
_MAX_URL_LENGTH = 8192
_MAX_PATH_LENGTH = 4096
_MATERIALIZED_INPUT_ROOT = "/workspace/.a13n/inputs"


class BinaryContentDelivery(StrEnum):
    auto = "auto"
    model_content = "model_content"
    model_url = "model_url"
    environment_path = "environment_path"


class UrlBinarySource(StrictModel):
    type: Literal["url"] = "url"
    url: Annotated[str, StringConstraints(min_length=1, max_length=_MAX_URL_LENGTH)]


class PathBinarySource(StrictModel):
    type: Literal["path"] = "path"
    environment_binding: BoundedKey
    path: Annotated[str, StringConstraints(min_length=1, max_length=_MAX_PATH_LENGTH)]

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        if "\\" in value or "\x00" in value or value.startswith("/"):
            raise ValueError("binary path must be a relative POSIX path")
        normalized = posixpath.normpath(value)
        if normalized in {"", ".", ".."} or normalized.startswith("../"):
            raise ValueError("binary path must stay within its Environment binding")
        return normalized


class AssetBinarySource(StrictModel):
    type: Literal["asset"] = "asset"
    asset_id: ObjectId


BinaryContentSource = Annotated[
    UrlBinarySource | PathBinarySource | AssetBinarySource,
    Field(discriminator="type"),
]


class TextContent(StrictModel):
    type: Literal["text"] = "text"
    text: Annotated[str, StringConstraints(min_length=1, max_length=_MAX_TEXT_LENGTH)]


def _optional_filename(value: str | None) -> str | None:
    return None if value is None else normalize_asset_filename(value)


def _optional_media_type(value: str | None) -> str | None:
    return None if value is None else normalize_media_type(value)


class BinaryContent(StrictModel):
    type: Literal["binary"] = "binary"
    source: BinaryContentSource
    filename: Annotated[str | None, AfterValidator(_optional_filename)] = None
    media_type: Annotated[str | None, AfterValidator(_optional_media_type)] = None
    delivery: BinaryContentDelivery = BinaryContentDelivery.auto


ContentBlock = Annotated[TextContent | BinaryContent, Field(discriminator="type")]


class AgentInput(StrictModel):
    """Submitted or retained versioned ordinary Agent input."""

    schema_version: Literal["1", "2"]
    content: tuple[ContentBlock, ...] = Field(default=(), max_length=_MAX_CONTENT_BLOCKS)
    structured_content: JsonValue | None = None

    @model_validator(mode="after")
    def source_union_matches_version(self) -> AgentInput:
        if self.schema_version == "1" and any(
            isinstance(block, BinaryContent) and isinstance(block.source, AssetBinarySource) for block in self.content
        ):
            raise ValueError("AgentInput version 1 does not support Asset sources")
        return self


class AcceptedBinaryContent(StrictModel):
    type: Literal["binary"] = "binary"
    source: BinaryContentSource
    filename: Annotated[str | None, AfterValidator(_optional_filename)] = None
    media_type: Annotated[str | None, AfterValidator(_optional_media_type)] = None
    delivery: Literal[
        BinaryContentDelivery.model_content,
        BinaryContentDelivery.model_url,
        BinaryContentDelivery.environment_path,
    ]

    @model_validator(mode="after")
    def delivery_shape_is_concrete(self) -> AcceptedBinaryContent:
        if self.delivery is BinaryContentDelivery.model_url and (
            not isinstance(self.source, UrlBinarySource) or self.media_type is None
        ):
            raise ValueError("accepted model_url input requires a URL source and media type")
        return self


AcceptedContentBlock = Annotated[TextContent | AcceptedBinaryContent, Field(discriminator="type")]


class AcceptedAgentInput(StrictModel):
    """Canonical Agent input after all acceptance-time choices are frozen."""

    schema_version: Literal["1", "2"]
    content: tuple[AcceptedContentBlock, ...] = Field(default=(), max_length=_MAX_CONTENT_BLOCKS)
    structured_content: JsonValue | None = None

    @model_validator(mode="after")
    def source_union_matches_version(self) -> AcceptedAgentInput:
        if self.schema_version == "1" and any(
            isinstance(block, AcceptedBinaryContent) and isinstance(block.source, AssetBinarySource)
            for block in self.content
        ):
            raise ValueError("AgentInput version 1 does not support Asset sources")
        return self

    def canonical_bytes(self) -> bytes:
        try:
            return rfc8785.dumps(self.model_dump(mode="json", by_alias=True, exclude_none=True))
        except rfc8785.CanonicalizationError as error:
            raise AgentInputError("input_not_canonicalizable", "Agent input is not canonical JSON") from error


class AgentInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AssetAuthorizer(Protocol):
    async def __call__(self, asset_id: str) -> Asset: ...


@dataclass(frozen=True, slots=True)
class AgentInputAcceptanceContext:
    workspace_id: str
    model_characteristics: HarnessModelCharacteristics
    max_input_bytes: int
    structured_content_schema: JsonObject | None = None
    environment_writable: bool = False
    environment_bindings: frozenset[str] = frozenset()
    allow_direct_model_urls: bool = True


class AgentInputAcceptance:
    """Resolve one submitted value without acquiring binary source bytes."""

    def __init__(
        self,
        endpoint_policy: EndpointPolicy,
        authorize_asset: AssetAuthorizer,
    ) -> None:
        self._endpoint_policy = endpoint_policy
        self._authorize_asset = authorize_asset

    async def accept(
        self,
        submitted: AgentInput,
        context: AgentInputAcceptanceContext,
    ) -> AcceptedAgentInput:
        self._validate_structured_content(submitted.structured_content, context)
        accepted = await gather(*(self._accept_block(block, context) for block in submitted.content))
        result = AcceptedAgentInput(
            schema_version=submitted.schema_version,
            content=tuple(accepted),
            structured_content=submitted.structured_content,
        )
        if len(result.canonical_bytes()) > context.max_input_bytes:
            raise AgentInputError("input_too_large", "Agent input exceeds the accepted input limit")
        return result

    async def _accept_block(
        self,
        block: ContentBlock,
        context: AgentInputAcceptanceContext,
    ) -> AcceptedContentBlock:
        return block if isinstance(block, TextContent) else await self._accept_binary(block, context)

    async def _accept_binary(
        self,
        block: BinaryContent,
        context: AgentInputAcceptanceContext,
    ) -> AcceptedBinaryContent:
        source = block.source
        asset: Asset | None = None
        if isinstance(source, UrlBinarySource):
            try:
                normalized_url = await self._endpoint_policy.validate(source.url)
            except EndpointPolicyError as error:
                raise AgentInputError("input_url_invalid", "Binary URL is not permitted") from error
            source = source.model_copy(update={"url": normalized_url})
        elif isinstance(source, PathBinarySource):
            if source.environment_binding not in context.environment_bindings:
                raise AgentInputError("input_path_unauthorized", "Environment binding is not authorized")
        else:
            asset = await self._authorize_asset(source.asset_id)
            if asset.workspace_id != context.workspace_id or asset.deleted_at is not None:
                raise AgentInputError("input_asset_unavailable", "Asset is not available for Agent input")
            if block.filename is not None and block.filename != asset.filename:
                raise AgentInputError("input_asset_metadata_conflict", "Asset filename does not match")
            if block.media_type is not None and block.media_type != asset.media_type:
                raise AgentInputError("input_asset_metadata_conflict", "Asset media type does not match")

        media_type = block.media_type or (asset.media_type if asset is not None else None)
        delivery = _resolve_delivery(block.delivery, source, media_type, context)
        return AcceptedBinaryContent(
            source=source,
            filename=block.filename,
            media_type=block.media_type,
            delivery=delivery,
        )

    @staticmethod
    def _validate_structured_content(
        value: JsonValue | None,
        context: AgentInputAcceptanceContext,
    ) -> None:
        if value is None or context.structured_content_schema is None:
            return
        validator = Draft202012Validator(context.structured_content_schema)
        if not validator.is_valid(value):
            raise AgentInputError(
                "structured_content_invalid",
                "Structured Agent input does not match the selected protocol schema",
            )


def _resolve_delivery(
    requested: BinaryContentDelivery,
    source: BinaryContentSource,
    media_type: str | None,
    context: AgentInputAcceptanceContext,
) -> Literal[
    BinaryContentDelivery.model_content,
    BinaryContentDelivery.model_url,
    BinaryContentDelivery.environment_path,
]:
    if requested is BinaryContentDelivery.auto:
        if _model_accepts(media_type, context.model_characteristics):
            requested = BinaryContentDelivery.model_content
        elif context.environment_writable:
            requested = BinaryContentDelivery.environment_path
        else:
            raise AgentInputError("input_delivery_unavailable", "No accepted binary delivery is available")
    if requested is BinaryContentDelivery.model_url:
        if not isinstance(source, UrlBinarySource) or media_type is None:
            raise AgentInputError("input_delivery_invalid", "model_url requires a URL source and media type")
        if not context.allow_direct_model_urls or not _model_accepts(media_type, context.model_characteristics):
            raise AgentInputError("input_delivery_unavailable", "The selected Model cannot accept this URL")
        return BinaryContentDelivery.model_url
    elif requested is BinaryContentDelivery.model_content:
        if not _model_accepts(media_type, context.model_characteristics):
            raise AgentInputError("input_delivery_unavailable", "The selected Model cannot accept this content")
        return BinaryContentDelivery.model_content
    elif requested is BinaryContentDelivery.environment_path:
        if not context.environment_writable:
            raise AgentInputError("input_delivery_unavailable", "A writable default Environment is required")
        return BinaryContentDelivery.environment_path
    else:
        raise AgentInputError("input_delivery_invalid", "Submission did not resolve to a concrete delivery")


def _model_accepts(media_type: str | None, characteristics: HarnessModelCharacteristics) -> bool:
    if media_type is None:
        return True
    required = _model_capability(media_type)
    return required is None or required in characteristics.capabilities


def _model_capability(media_type: str) -> ModelCapability | None:
    if media_type.startswith("image/"):
        return ModelCapability.IMAGE_UNDERSTANDING
    if media_type.startswith("video/"):
        return ModelCapability.VIDEO_UNDERSTANDING
    if media_type.startswith("audio/"):
        return ModelCapability.AUDIO_UNDERSTANDING
    return None


@dataclass(frozen=True, slots=True)
class AcquiredBinary:
    chunks: AsyncIterable[bytes]
    media_type: str | None


class BinarySourceReader(Protocol):
    async def open(
        self,
        source: BinaryContentSource,
        *,
        max_bytes: int,
    ) -> AcquiredBinary: ...


class EnvironmentInputWriter(Protocol):
    async def replace(self, path: str, chunks: AsyncIterable[bytes]) -> None: ...


class InputAdapter(Protocol):
    def __call__(
        self,
        content: Sequence[UserContent],
        structured_content: JsonValue | None,
        config: Mapping[str, JsonValue],
    ) -> RunInputValue | None: ...


class AgentInputMapper:
    """Acquire accepted binary sources and construct one native Harness input."""

    def __init__(
        self,
        source_reader: BinarySourceReader,
        adapters: Mapping[str, InputAdapter],
        *,
        max_binary_bytes: int,
    ) -> None:
        self._source_reader = source_reader
        self._adapters = dict(adapters)
        self._max_binary_bytes = max_binary_bytes

    async def map(
        self,
        accepted: AcceptedAgentInput,
        *,
        input_instance_id: str,
        adapter: InputAdapterConfig,
        environment: EnvironmentInputWriter | None,
    ) -> RunInputValue | None:
        native: list[UserContent] = []
        for index, block in enumerate(accepted.content):
            if isinstance(block, TextContent):
                native.append(block.text)
                continue
            native.append(
                await self._map_binary(
                    block,
                    block_index=index,
                    input_instance_id=input_instance_id,
                    environment=environment,
                )
            )
        selected = self._adapters.get(adapter.adapter_key)
        if selected is None:
            raise AgentInputError("input_adapter_unavailable", "The locked input adapter is unavailable")
        return selected(tuple(native), accepted.structured_content, adapter.config)

    async def _map_binary(
        self,
        block: AcceptedBinaryContent,
        *,
        block_index: int,
        input_instance_id: str,
        environment: EnvironmentInputWriter | None,
    ) -> UserContent:
        media_type = block.media_type
        if block.delivery is BinaryContentDelivery.model_url:
            if not isinstance(block.source, UrlBinarySource) or media_type is None:
                raise AgentInputError("input_delivery_invalid", "Accepted model_url input is invalid")
            return _native_url(block.source.url, media_type, block.filename)

        acquired = await self._source_reader.open(block.source, max_bytes=self._max_binary_bytes)
        effective_media_type = media_type or acquired.media_type or "application/octet-stream"
        if media_type is not None and acquired.media_type is not None and media_type != acquired.media_type:
            raise AgentInputError("input_media_type_conflict", "Binary bytes conflict with the accepted media type")
        if block.delivery is BinaryContentDelivery.model_content:
            return NativeBinaryContent(
                data=await _collect_bounded(acquired.chunks, self._max_binary_bytes),
                media_type=effective_media_type,
                identifier=block.filename,
            )
        if environment is None:
            raise AgentInputError("input_environment_unavailable", "The selected Environment is unavailable")
        path = materialized_input_path(input_instance_id, block_index)
        await environment.replace(path, _bounded_chunks(acquired.chunks, self._max_binary_bytes))
        return path


def native_input_adapter(
    content: Sequence[UserContent],
    structured_content: JsonValue | None,
    config: Mapping[str, JsonValue],
) -> RunInputValue | None:
    if config:
        raise AgentInputError("input_adapter_invalid", "The native input adapter does not accept configuration")
    values = list(content)
    if structured_content is not None:
        try:
            values.append(rfc8785.dumps(structured_content).decode("utf-8"))
        except rfc8785.CanonicalizationError as error:
            raise AgentInputError("structured_content_invalid", "Structured input is not canonicalizable") from error
    if not values:
        return None
    if len(values) == 1 and isinstance(values[0], str):
        return values[0]
    return tuple(values)


def materialized_input_path(input_instance_id: str, block_index: int) -> str:
    if not input_instance_id or "/" in input_instance_id or input_instance_id in {".", ".."}:
        raise ValueError("input instance ID is not path-safe")
    if block_index < 0:
        raise ValueError("binary block index must be non-negative")
    return f"{_MATERIALIZED_INPUT_ROOT}/{input_instance_id}/content-{block_index}"


def decode_agent_input(body: bytes) -> AgentInput:
    """Decode strict UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""

    try:
        payload = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, _StrictJsonError) as error:
        raise AgentInputError("input_json_invalid", "Agent input must be strict UTF-8 JSON") from error
    try:
        return TypeAdapter(AgentInput).validate_python(payload)
    except ValidationError as error:
        raise AgentInputError("input_schema_invalid", "Agent input does not match a supported schema") from error


def _native_url(url: str, media_type: str, filename: str | None) -> UserContent:
    if media_type.startswith("image/"):
        return ImageUrl(url, media_type=media_type, identifier=filename)
    if media_type.startswith("audio/"):
        return AudioUrl(url, media_type=media_type, identifier=filename)
    if media_type.startswith("video/"):
        return VideoUrl(url, media_type=media_type, identifier=filename)
    return DocumentUrl(url, media_type=media_type, identifier=filename)


async def _collect_bounded(chunks: AsyncIterable[bytes], limit: int) -> bytes:
    collected = bytearray()
    async for chunk in _bounded_chunks(chunks, limit):
        collected.extend(chunk)
    return bytes(collected)


async def _bounded_chunks(chunks: AsyncIterable[bytes], limit: int) -> AsyncIterable[bytes]:
    consumed = 0
    async for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise AgentInputError("input_binary_invalid", "Binary source yielded a non-bytes chunk")
        consumed += len(chunk)
        if consumed > limit:
            raise AgentInputError("input_binary_too_large", "Binary source exceeds the execution limit")
        yield chunk


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJsonError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise _StrictJsonError(f"invalid JSON number {value}")


class _StrictJsonError(ValueError):
    pass


__all__ = [
    "AcceptedAgentInput",
    "AcceptedBinaryContent",
    "AcquiredBinary",
    "AgentInput",
    "AgentInputAcceptance",
    "AgentInputAcceptanceContext",
    "AgentInputError",
    "AgentInputMapper",
    "AssetBinarySource",
    "BinaryContent",
    "BinaryContentDelivery",
    "BinaryContentSource",
    "BinarySourceReader",
    "ContentBlock",
    "EnvironmentInputWriter",
    "InputAdapter",
    "PathBinarySource",
    "TextContent",
    "UrlBinarySource",
    "decode_agent_input",
    "materialized_input_path",
    "native_input_adapter",
]
