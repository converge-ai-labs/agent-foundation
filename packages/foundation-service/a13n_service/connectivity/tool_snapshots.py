"""Catalog decoding and immutable Run-owned MCP tool snapshots."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from functools import partial
from typing import Literal

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from a13n_service.connectivity.bounds import CATALOG_MAX_BYTES
from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.interactions import MCPToolSnapshotRef
from a13n_service.storage import ObjectConflict, ObjectInfo, ObjectStore, ObjectStoreError

from .selection_domain import MCPToolSnapshot, SnapshotBinding, SnapshotTool, SourceKind

SNAPSHOT_CONTENT_TYPE = "application/vnd.a13n.mcp-tool-snapshot+json"
_IDENTIFIER_CHARACTER = re.compile(r"[^A-Za-z0-9_]+")


class ToolSnapshotError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CatalogTool(_StrictModel):
    key: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=16_384)
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    annotations: JsonObject = Field(default_factory=dict)


class _ConnectorCatalog(_StrictModel):
    schema_version: Literal["1"]
    source_kind: Literal["connector_connection"]
    connector_connection_id: str
    compatibility_profile: str
    provider_version: str
    connector_credential_generation: int = Field(ge=1)
    connection_setup_generation: int = Field(ge=1)
    tools: tuple[CatalogTool, ...] = Field(max_length=2_048)


class _MCPServer(_StrictModel):
    name: str
    version: str


class _MCPTool(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=16_384)
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    annotations: JsonObject = Field(default_factory=dict)


class _MCPCatalog(_StrictModel):
    schema_version: Literal["1"]
    source_kind: Literal["mcp_connection"]
    mcp_connection_id: str
    protocol_revision: Literal["2025-11-25"]
    credential_generation: int = Field(ge=0)
    server: _MCPServer
    capabilities: JsonObject
    tools: tuple[_MCPTool, ...] = Field(max_length=2_048)


@dataclass(frozen=True, slots=True)
class CatalogObject:
    source_kind: SourceKind
    source_id: str
    object_key: str
    digest_sha256: str
    size_bytes: int
    credential_generation: int
    source_generation: int
    compatibility_profile: str


@dataclass(frozen=True, slots=True)
class SelectedCatalog:
    source_kind: SourceKind
    source_alias: str
    selection_index: int
    exposure: Literal["direct", "catalog"]
    tools: tuple[CatalogTool, ...]


@dataclass(frozen=True, slots=True)
class StoredToolSnapshot:
    reference: MCPToolSnapshotRef
    object_key: str
    snapshot: MCPToolSnapshot


async def read_catalog(objects: ObjectStore, selected: CatalogObject) -> tuple[CatalogTool, ...]:
    try:
        body, info = await _read_object(objects, selected.object_key, max_bytes=CATALOG_MAX_BYTES)
        _verify_object(info, selected, body)
        if selected.source_kind == "connector_connection":
            catalog = _ConnectorCatalog.model_validate_json(body)
            if (
                catalog.connector_connection_id != selected.source_id
                or catalog.connector_credential_generation != selected.credential_generation
                or catalog.connection_setup_generation != selected.source_generation
                or catalog.compatibility_profile != selected.compatibility_profile
            ):
                raise ToolSnapshotError("catalog_identity_mismatch")
            return catalog.tools
        catalog = _MCPCatalog.model_validate_json(body)
        if (
            catalog.mcp_connection_id != selected.source_id
            or catalog.credential_generation != selected.credential_generation
            or catalog.protocol_revision != selected.compatibility_profile
        ):
            raise ToolSnapshotError("catalog_identity_mismatch")
        return tuple(
            CatalogTool(
                key=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
                annotations=tool.annotations,
            )
            for tool in catalog.tools
        )
    except (ObjectStoreError, ValidationError, ValueError) as error:
        raise ToolSnapshotError("catalog_object_invalid") from error


async def build_snapshot(run_id: str, catalogs: tuple[SelectedCatalog, ...]) -> tuple[MCPToolSnapshot, bytes]:
    return await to_thread.run_sync(partial(_build_snapshot, run_id, catalogs))


class MCPToolSnapshotStore:
    def __init__(self, objects: ObjectStore) -> None:
        self._objects = objects

    async def retain(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        run_id: str,
        snapshot: MCPToolSnapshot,
        body: bytes,
    ) -> StoredToolSnapshot:
        digest = hashlib.sha256(body).hexdigest()
        key = snapshot_key(organization_id, workspace_id, run_id, digest)
        metadata = {
            "schema-version": snapshot.schema_version,
            "run-id": run_id,
            "digest-sha256": digest,
        }
        try:
            try:
                info = await self._objects.put(
                    key,
                    body,
                    content_type=SNAPSHOT_CONTENT_TYPE,
                    metadata=metadata,
                    if_none_match=True,
                )
            except ObjectConflict:
                info = await self._objects.stat(key)
            if (
                info.key != key
                or info.size != len(body)
                or info.content_type != SNAPSHOT_CONTENT_TYPE
                or dict(info.metadata) != metadata
            ):
                raise ToolSnapshotError("snapshot_object_conflict")
        except ObjectStoreError as error:
            raise ToolSnapshotError("snapshot_storage_unavailable") from error
        return StoredToolSnapshot(
            reference=MCPToolSnapshotRef(
                digest_sha256=digest,
                size_bytes=len(body),
                content_type=SNAPSHOT_CONTENT_TYPE,
                schema_version=snapshot.schema_version,
            ),
            object_key=key,
            snapshot=snapshot,
        )


def snapshot_key(organization_id: str, workspace_id: str, run_id: str, digest_sha256: str) -> str:
    return f"tenants/{organization_id}/workspaces/{workspace_id}/runs/{run_id}/mcp-tool-snapshots/{digest_sha256}.json"


def _build_snapshot(run_id: str, catalogs: tuple[SelectedCatalog, ...]) -> tuple[MCPToolSnapshot, bytes]:
    direct: list[SnapshotTool] = []
    catalog_tools: list[SnapshotTool] = []
    for selected in catalogs:
        for tool in selected.tools:
            identity = canonical_json(
                {
                    "run_id": run_id,
                    "source_kind": selected.source_kind,
                    "selection_index": selected.selection_index,
                    "tool_key": tool.key,
                }
            )
            digest = hashlib.sha256(identity.encode()).hexdigest()
            item = SnapshotTool(
                reference=f"mcpref_{digest}",
                source_alias=selected.source_alias,
                source_tool_name=tool.key,
                model_name=(
                    _model_name(selected.source_alias, tool.key, digest) if selected.exposure == "direct" else None
                ),
                description=tool.description,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
                annotations=tool.annotations,
                binding=SnapshotBinding(
                    source_kind=selected.source_kind,
                    selection_index=selected.selection_index,
                    tool_key=tool.key,
                ),
            )
            (direct if selected.exposure == "direct" else catalog_tools).append(item)
    snapshot = MCPToolSnapshot(
        direct_tools=tuple(sorted(direct, key=lambda item: item.model_name or "")),
        catalog_tools=tuple(sorted(catalog_tools, key=lambda item: item.reference)),
    )
    body = canonical_json(snapshot.model_dump(mode="json")).encode()
    if not body or len(body) > CATALOG_MAX_BYTES:
        raise ToolSnapshotError("snapshot_too_large")
    return snapshot, body


def _model_name(alias: str, tool_key: str, digest: str) -> str:
    alias_part = _identifier_part(alias, 16)
    tool_part = _identifier_part(tool_key, 32)
    return f"ext_{alias_part}_{tool_part}_{digest}"


def _identifier_part(value: str, limit: int) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    normalized = _IDENTIFIER_CHARACTER.sub("_", ascii_value).strip("_").lower()
    return (normalized or "tool")[:limit]


async def _read_object(objects: ObjectStore, key: str, *, max_bytes: int) -> tuple[bytes, ObjectInfo]:
    async with objects.open(key) as reader:
        info = reader.info
        if info.size < 1 or info.size > max_bytes:
            raise ToolSnapshotError("catalog_object_invalid")
        chunks: list[bytes] = []
        size = 0
        async for chunk in reader:
            size += len(chunk)
            if size > max_bytes:
                raise ToolSnapshotError("catalog_object_invalid")
            chunks.append(chunk)
    body = b"".join(chunks)
    if len(body) != info.size:
        raise ToolSnapshotError("catalog_object_invalid")
    return body, info


def _verify_object(info: ObjectInfo, selected: CatalogObject, body: bytes) -> None:
    if (
        info.key != selected.object_key
        or info.size != selected.size_bytes
        or info.content_type != "application/json"
        or len(body) != selected.size_bytes
        or hashlib.sha256(body).hexdigest() != selected.digest_sha256
    ):
        raise ToolSnapshotError("catalog_object_invalid")


__all__ = [
    "CatalogObject",
    "CatalogTool",
    "MCPToolSnapshotStore",
    "SelectedCatalog",
    "StoredToolSnapshot",
    "ToolSnapshotError",
    "build_snapshot",
    "read_catalog",
    "snapshot_key",
]
