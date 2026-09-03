from __future__ import annotations

from collections.abc import AsyncIterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessModelCharacteristics, ModelCapability, RunInputValue
from a13n_service.agents.domain import InputAdapterConfig, JsonObject
from a13n_service.assets.domain import Asset, UploadedAssetSource
from a13n_service.connectivity.outbound_policy import EndpointPolicy
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.interactions.input import (
    AcceptedAgentInput,
    AcquiredBinary,
    AgentInput,
    AgentInputAcceptance,
    AgentInputAcceptanceContext,
    AgentInputError,
    AgentInputMapper,
    AssetBinarySource,
    BinaryContentDelivery,
    BinaryContentSource,
    EnvironmentInputWriter,
    PathBinarySource,
    UrlBinarySource,
    decode_agent_input,
    materialized_input_path,
    native_input_adapter,
)
from pydantic import JsonValue, ValidationError
from pydantic_ai import BinaryContent as NativeBinaryContent
from pydantic_ai.messages import UserContent

NOW = datetime(2026, 9, 3, tzinfo=UTC)
WORKSPACE_ID = "ws_1234567890abcdef"
ASSET_ID = "ast_1234567890abcdef"


def _asset() -> Asset:
    return Asset(
        id=ASSET_ID,
        organization_id="org_1234567890abcdef",
        workspace_id=WORKSPACE_ID,
        filename="diagram.png",
        media_type="image/png",
        size_bytes=4,
        content_sha256="a" * 64,
        source=UploadedAssetSource(
            principal=PrincipalRef(
                principal_type=PrincipalType.user,
                principal_id="usr_1234567890abcdef",
            )
        ),
        created_at=NOW,
        deleted_at=None,
    )


def _context(
    *,
    max_input_bytes: int = 4096,
    structured_content_schema: JsonObject | None = None,
    environment_writable: bool = True,
) -> AgentInputAcceptanceContext:
    return AgentInputAcceptanceContext(
        workspace_id=WORKSPACE_ID,
        model_characteristics=HarnessModelCharacteristics(capabilities={ModelCapability.IMAGE_UNDERSTANDING}),
        max_input_bytes=max_input_bytes,
        structured_content_schema=structured_content_schema,
        environment_writable=environment_writable,
        environment_bindings=frozenset({"sandbox"}),
    )


def _acceptance(asset: Asset | None = None) -> AgentInputAcceptance:
    async def authorize(asset_id: str) -> Asset:
        if asset is None or asset.id != asset_id:
            raise AgentInputError("input_asset_unavailable", "missing")
        return asset

    return AgentInputAcceptance(EndpointPolicy(), authorize)


def test_agent_input_versions_and_strict_json_are_closed() -> None:
    with pytest.raises(ValidationError, match="does not support Asset"):
        AgentInput.model_validate(
            {
                "schema_version": "1",
                "content": [{"type": "binary", "source": {"type": "asset", "asset_id": ASSET_ID}}],
            }
        )
    with pytest.raises(ValidationError):
        AgentInput.model_validate({"schema_version": "3"})
    with pytest.raises(AgentInputError, match="strict UTF-8 JSON"):
        decode_agent_input(b'{"schema_version":"2","schema_version":"1"}')
    with pytest.raises(AgentInputError, match="strict UTF-8 JSON"):
        decode_agent_input(b'{"schema_version":"2","structured_content":NaN}')


def test_path_and_media_metadata_are_normalized_at_the_wire_boundary() -> None:
    submitted = AgentInput.model_validate(
        {
            "schema_version": "2",
            "content": [
                {
                    "type": "binary",
                    "source": {"type": "path", "environment_binding": "sandbox", "path": "a//b.txt"},
                    "filename": "input.txt",
                    "media_type": "TEXT/PLAIN",
                }
            ],
        }
    )

    block = submitted.content[0]
    assert block.type == "binary"
    assert block.source == PathBinarySource(environment_binding="sandbox", path="a/b.txt")
    assert block.media_type == "text/plain"
    with pytest.raises(ValidationError):
        PathBinarySource(environment_binding="sandbox", path="../secret")
    with pytest.raises(ValidationError):
        PathBinarySource(environment_binding="sandbox", path="/etc/passwd")


@pytest.mark.anyio
async def test_acceptance_freezes_url_and_asset_delivery_without_reading_bytes() -> None:
    service = _acceptance(_asset())
    submitted = AgentInput.model_validate(
        {
            "schema_version": "2",
            "content": [
                {
                    "type": "binary",
                    "source": {"type": "url", "url": "HTTPS://1.1.1.1:443/file.png"},
                    "media_type": "image/png",
                    "delivery": "model_url",
                },
                {
                    "type": "binary",
                    "source": {"type": "asset", "asset_id": ASSET_ID},
                },
            ],
        }
    )

    accepted = await service.accept(submitted, _context())

    first, second = accepted.content
    assert first.type == "binary" and second.type == "binary"
    assert first.source == UrlBinarySource(url="https://1.1.1.1/file.png")
    assert first.delivery is BinaryContentDelivery.model_url
    assert second.source == AssetBinarySource(asset_id=ASSET_ID)
    assert second.delivery is BinaryContentDelivery.model_content
    assert second.filename is None and second.media_type is None
    assert b'"delivery":"auto"' not in accepted.canonical_bytes()


@pytest.mark.anyio
async def test_acceptance_enforces_asset_metadata_schema_limits_and_delivery_feasibility() -> None:
    service = _acceptance(_asset())
    conflict = AgentInput.model_validate(
        {
            "schema_version": "2",
            "content": [
                {
                    "type": "binary",
                    "source": {"type": "asset", "asset_id": ASSET_ID},
                    "filename": "different.png",
                }
            ],
        }
    )
    with pytest.raises(AgentInputError, match="filename does not match"):
        await service.accept(conflict, _context())

    structured = AgentInput(schema_version="2", structured_content={"answer": "not-an-integer"})
    with pytest.raises(AgentInputError, match="does not match"):
        await service.accept(
            structured,
            _context(
                structured_content_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "integer"}},
                    "required": ["answer"],
                }
            ),
        )

    unknown_media = AgentInput.model_validate(
        {
            "schema_version": "2",
            "content": [
                {
                    "type": "binary",
                    "source": {"type": "path", "environment_binding": "sandbox", "path": "blob"},
                }
            ],
        }
    )
    accepted = await service.accept(unknown_media, _context())
    assert accepted.content[0].type == "binary"
    assert accepted.content[0].delivery is BinaryContentDelivery.model_content
    with pytest.raises(AgentInputError, match="exceeds"):
        await service.accept(structured, _context(max_input_bytes=10))


async def _chunks(*values: bytes) -> AsyncIterable[bytes]:
    for value in values:
        yield value


@dataclass
class _Reader:
    chunks: tuple[bytes, ...]
    media_type: str | None
    sources: list[BinaryContentSource] = field(default_factory=list)

    async def open(self, source: BinaryContentSource, *, max_bytes: int) -> AcquiredBinary:
        self.sources.append(source)
        return AcquiredBinary(_chunks(*self.chunks), self.media_type)


@dataclass
class _Writer(EnvironmentInputWriter):
    writes: list[tuple[str, bytes]] = field(default_factory=list)

    async def replace(self, path: str, chunks: AsyncIterable[bytes]) -> None:
        self.writes.append((path, b"".join([chunk async for chunk in chunks])))


def _recording_adapter(
    seen: list[tuple[tuple[UserContent, ...], JsonValue | None]],
):
    def adapt(
        content: Sequence[UserContent],
        structured_content: JsonValue | None,
        config: Mapping[str, JsonValue],
    ) -> RunInputValue | None:
        assert config == {}
        seen.append((tuple(content), structured_content))
        return tuple(content)

    return adapt


@pytest.mark.anyio
async def test_mapper_preserves_order_and_materializes_only_environment_delivery() -> None:
    accepted = AcceptedAgentInput.model_validate(
        {
            "schema_version": "2",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "binary",
                    "source": {"type": "asset", "asset_id": ASSET_ID},
                    "filename": "diagram.png",
                    "media_type": "image/png",
                    "delivery": "model_content",
                },
                {
                    "type": "binary",
                    "source": {"type": "path", "environment_binding": "sandbox", "path": "data.csv"},
                    "media_type": "text/csv",
                    "delivery": "environment_path",
                },
            ],
            "structured_content": {"purpose": "review"},
        }
    )
    reader = _Reader((b"da", b"ta"), None)
    writer = _Writer()
    seen: list[tuple[tuple[UserContent, ...], JsonValue | None]] = []
    mapper = AgentInputMapper(reader, {"test": _recording_adapter(seen)}, max_binary_bytes=16)

    result = await mapper.map(
        accepted,
        input_instance_id="run_1234567890abcdef",
        adapter=InputAdapterConfig(adapter_key="test"),
        environment=writer,
    )

    assert result is not None
    content, structured = seen[0]
    assert content[0] == "look"
    assert isinstance(content[1], NativeBinaryContent) and content[1].data == b"data"
    assert content[2] == "/workspace/.a13n/inputs/run_1234567890abcdef/content-2"
    assert structured == {"purpose": "review"}
    assert writer.writes == [(content[2], b"data")]
    assert len(reader.sources) == 2


@pytest.mark.anyio
async def test_mapper_rejects_oversize_or_mismatched_binary_and_native_adapter_handles_empty_input() -> None:
    accepted = AcceptedAgentInput.model_validate(
        {
            "schema_version": "2",
            "content": [
                {
                    "type": "binary",
                    "source": {"type": "asset", "asset_id": ASSET_ID},
                    "media_type": "image/png",
                    "delivery": "model_content",
                }
            ],
        }
    )
    mapper = AgentInputMapper(_Reader((b"12345",), "image/png"), {"native": native_input_adapter}, max_binary_bytes=4)
    with pytest.raises(AgentInputError, match="exceeds"):
        await mapper.map(
            accepted,
            input_instance_id="run_1234567890abcdef",
            adapter=InputAdapterConfig(adapter_key="native"),
            environment=None,
        )

    mismatch = AgentInputMapper(_Reader((b"data",), "image/jpeg"), {"native": native_input_adapter}, max_binary_bytes=4)
    with pytest.raises(AgentInputError, match="conflict"):
        await mismatch.map(
            accepted,
            input_instance_id="run_1234567890abcdef",
            adapter=InputAdapterConfig(adapter_key="native"),
            environment=None,
        )

    assert native_input_adapter((), None, {}) is None
    assert native_input_adapter((), {"b": 1, "a": 2}, {}) == '{"a":2,"b":1}'
    assert materialized_input_path("steer_1234567890abcdef", 0).endswith("/content-0")
