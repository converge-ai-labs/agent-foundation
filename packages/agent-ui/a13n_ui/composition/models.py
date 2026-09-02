"""Strict immutable Agent and Environment composition snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_ui.configuration.models import EnvironmentVariableSource, McpTransport

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class SnapshotModel(BaseModel):
    """Immutable strict base for persisted composition values."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_serialized_tuples(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            if isinstance(normalized.get(name), list) and _contains_tuple(field.annotation):
                normalized[name] = tuple(normalized[name])
        return normalized


class DependencyLock(SnapshotModel):
    """Exact installed provenance for one trusted reconstruction boundary."""

    dependency_kind: Literal["model-adapter", "plugin", "mcp-adapter", "provider", "workspace-binder"]
    key: str = Field(min_length=1, max_length=200)
    class_module: str = Field(min_length=1, max_length=512)
    class_qualname: str = Field(min_length=1, max_length=512)
    import_target: str | None = Field(default=None, min_length=1, max_length=1024)
    distribution_name: str = Field(min_length=1, max_length=256)
    distribution_version: str = Field(min_length=1, max_length=128)


class ResolvedModelRecipe(SnapshotModel):
    """One credential-free native Model reconstruction recipe."""

    adapter_key: str = Field(min_length=1, max_length=200)
    route: str = Field(min_length=3, max_length=512)
    api_key: EnvironmentVariableSource | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_cfg: dict[str, JsonValue] = Field(default_factory=dict)
    adapter_lock: DependencyLock

    @model_validator(mode="after")
    def _matching_adapter(self) -> Self:
        if self.adapter_lock.dependency_kind != "model-adapter" or self.adapter_lock.key != self.adapter_key:
            raise ValueError("Model adapter lock does not match its recipe")
        return self


class ResolvedPluginRecipe(SnapshotModel):
    """One selected Harness Plugin instance and normalized package-owned configuration."""

    instance_name: str = Field(min_length=1, max_length=64)
    plugin_key: str = Field(min_length=1, max_length=200)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    factory_lock: DependencyLock

    @model_validator(mode="after")
    def _matching_factory(self) -> Self:
        if self.factory_lock.dependency_kind != "plugin" or self.factory_lock.key != self.plugin_key:
            raise ValueError("Plugin factory lock does not match its recipe")
        return self


class ResolvedMcpRecipe(SnapshotModel):
    """One inert MCP transport recipe with credential references only."""

    server_name: str = Field(min_length=1, max_length=64)
    transport: McpTransport
    adapter_lock: DependencyLock

    @model_validator(mode="after")
    def _matching_adapter(self) -> Self:
        if self.adapter_lock.dependency_kind != "mcp-adapter":
            raise ValueError("MCP recipe requires an MCP adapter lock")
        return self


class ResolvedAgentNode(SnapshotModel):
    """One complete resolved Agent definition inside a finite immutable tree."""

    definition_digest: str = Field(pattern=_DIGEST_PATTERN)
    source_kind: Literal["agent", "markdown"]
    source_name: str = Field(min_length=1, max_length=64)
    instructions: tuple[str, ...] = Field(min_length=1, max_length=32)
    model: ResolvedModelRecipe
    plugins: tuple[ResolvedPluginRecipe, ...] = Field(default=(), max_length=128)
    mcp_servers: tuple[ResolvedMcpRecipe, ...] = Field(default=(), max_length=128)
    tools: tuple[str, ...] | None = Field(default=None, max_length=128)
    optional_tools: tuple[str, ...] | None = Field(default=None, max_length=128)
    children: tuple[ResolvedSubagent, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def _valid_node(self) -> Self:
        names = tuple(child.name for child in self.children)
        if len(names) != len(set(names)):
            raise ValueError("resolved child names must be unique")
        plugin_names = tuple(item.instance_name for item in self.plugins)
        if len(plugin_names) != len(set(plugin_names)):
            raise ValueError("resolved Plugin instance names must be unique")
        mcp_names = tuple(item.server_name for item in self.mcp_servers)
        if len(mcp_names) != len(set(mcp_names)):
            raise ValueError("resolved MCP server names must be unique")
        if self.source_kind == "agent" and (self.tools is not None or self.optional_tools is not None):
            raise ValueError("named Agents cannot serialize Markdown tool narrowing")
        if _node_digest(self) != self.definition_digest:
            raise ValueError("resolved Agent definition digest does not match its content")
        return self


class ResolvedSubagent(SnapshotModel):
    """One model-facing roster edge and its complete child definition."""

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=4096)
    instruction: str | None = Field(default=None, min_length=1, max_length=16 * 1024)
    definition: ResolvedAgentNode

    @model_validator(mode="after")
    def _matching_name(self) -> Self:
        if self.name != self.definition.source_name:
            raise ValueError("subagent edge name must match its resolved definition")
        return self


class ResolvedAgentSnapshot(SnapshotModel):
    """Complete finite Agent graph pinned by one immutable object reference."""

    schema_version: Literal["1"] = "1"
    harness_release: str = Field(min_length=1, max_length=128)
    package_prompt_version: str = Field(min_length=1, max_length=128)
    package_prompt_digest: str = Field(pattern=_DIGEST_PATTERN)
    dependencies: tuple[DependencyLock, ...]
    root: ResolvedAgentNode

    @model_validator(mode="after")
    def _complete_dependency_set(self) -> Self:
        if not self.root.instructions or _text_digest(self.root.instructions[0]) != self.package_prompt_digest:
            raise ValueError("root prompt does not match the locked package prompt")
        actual = tuple(sorted(self.dependencies, key=_dependency_sort_key))
        if actual != self.dependencies or len(actual) != len(set(actual)):
            raise ValueError("snapshot dependencies must be sorted and unique")
        required = _node_dependencies(self.root)
        if set(actual) != required:
            raise ValueError("snapshot dependency locks do not exactly cover the resolved graph")
        _require_bounded_tree(self.root)
        return self


class ResolvedEnvironmentProfile(SnapshotModel):
    """Credential-free profile template accepted for later per-folder binding."""

    schema_version: Literal["1"] = "1"
    profile_name: str = Field(min_length=1, max_length=64)
    kind: Literal["native", "local_eip", "provider"]
    provider_key: str = Field(min_length=1, max_length=200)
    provider_schema_version: str = Field(min_length=1, max_length=64)
    binder_key: str = Field(min_length=1, max_length=200)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    provider_lock: DependencyLock
    binder_lock: DependencyLock

    @model_validator(mode="after")
    def _matching_locks(self) -> Self:
        if self.provider_lock.dependency_kind != "provider" or self.provider_lock.key != self.provider_key:
            raise ValueError("Environment Provider lock does not match its profile")
        if self.binder_lock.dependency_kind != "workspace-binder" or self.binder_lock.key != self.binder_key:
            raise ValueError("workspace binder lock does not match its profile")
        return self


def resolved_agent_node(
    *,
    source_kind: Literal["agent", "markdown"],
    source_name: str,
    instructions: tuple[str, ...],
    model: ResolvedModelRecipe,
    plugins: tuple[ResolvedPluginRecipe, ...] = (),
    mcp_servers: tuple[ResolvedMcpRecipe, ...] = (),
    tools: tuple[str, ...] | None = None,
    optional_tools: tuple[str, ...] | None = None,
    children: tuple[ResolvedSubagent, ...] = (),
) -> ResolvedAgentNode:
    """Construct one node with its canonical behavior digest."""

    values: dict[str, object] = {
        "definition_digest": "0" * 64,
        "source_kind": source_kind,
        "source_name": source_name,
        "instructions": instructions,
        "model": model,
        "plugins": plugins,
        "mcp_servers": mcp_servers,
        "tools": tools,
        "optional_tools": optional_tools,
        "children": children,
    }
    digest = _canonical_digest({key: value for key, value in values.items() if key != "definition_digest"})
    values["definition_digest"] = digest
    return ResolvedAgentNode.model_validate(values)


def dependency_sort_key(lock: DependencyLock) -> tuple[str, str, str, str, str]:
    """Return the canonical dependency-lock ordering used by snapshots."""

    return _dependency_sort_key(lock)


def _contains_tuple(annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin is tuple:
        return True
    return any(_contains_tuple(argument) for argument in get_args(annotation))


def _node_digest(node: ResolvedAgentNode) -> str:
    return _canonical_digest(node.model_dump(mode="json", exclude={"definition_digest"}))


def _node_dependencies(root: ResolvedAgentNode) -> set[DependencyLock]:
    result: set[DependencyLock] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        result.add(node.model.adapter_lock)
        result.update(item.factory_lock for item in node.plugins)
        result.update(item.adapter_lock for item in node.mcp_servers)
        stack.extend(child.definition for child in reversed(node.children))
    return result


def _require_bounded_tree(root: ResolvedAgentNode) -> None:
    count = 0
    stack: list[tuple[ResolvedAgentNode, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 1024 or depth > 128:
            raise ValueError("resolved Agent graph exceeds its size or depth bound")
        stack.extend((child.definition, depth + 1) for child in reversed(node.children))


def _dependency_sort_key(lock: DependencyLock) -> tuple[str, str, str, str, str]:
    return (
        lock.dependency_kind,
        lock.key,
        lock.distribution_name,
        lock.distribution_version,
        lock.import_target or "",
    )


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_digest(value: object) -> str:
    def default(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, tuple):
            return list(item)
        raise TypeError(f"unsupported canonical value: {type(item).__name__}")

    encoded = json.dumps(
        value,
        default=default,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


ResolvedAgentNode.model_rebuild()
ResolvedSubagent.model_rebuild()

__all__ = [
    "DependencyLock",
    "ResolvedAgentNode",
    "ResolvedAgentSnapshot",
    "ResolvedEnvironmentProfile",
    "ResolvedMcpRecipe",
    "ResolvedModelRecipe",
    "ResolvedPluginRecipe",
    "ResolvedSubagent",
    "dependency_sort_key",
    "resolved_agent_node",
]
