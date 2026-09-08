"""Run-scoped headers for fresh upstream Pydantic AI MCP capabilities."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP, AbstractCapability

from a13n_harness._json import dump_json_text
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, RunError


class MCPHeadersFactory(Protocol):
    """Resolve exact outbound MCP headers for one logical Harness run."""

    def __call__(
        self,
        context: AgentContext,
    ) -> Mapping[str, str] | Awaitable[Mapping[str, str]]: ...


@dataclass(frozen=True, slots=True)
class MCPContextHeaderBinding:
    """Map one outbound header to one exact trusted context selector."""

    source: str
    required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip() or not _is_supported_source(self.source):
            raise DefinitionError(
                "MCP context header source is unsupported.",
                code="mcp_context_header_invalid",
                details={"source": self.source} if isinstance(self.source, str) else {},
            )
        if not isinstance(self.required, bool):
            raise DefinitionError(
                "MCP context header required must be a boolean.",
                code="mcp_context_header_invalid",
                details={"source": self.source},
            )


@dataclass(frozen=True, slots=True)
class MCPContextHeadersConfig:
    """Immutable exact outbound-header bindings for MCP run context."""

    headers: Mapping[str, MCPContextHeaderBinding] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: dict[str, MCPContextHeaderBinding] = {}
        folded_names: set[str] = set()
        for name, binding in self.headers.items():
            if not isinstance(name, str) or not name.strip():
                raise DefinitionError(
                    "MCP context header names must be non-blank strings.",
                    code="mcp_context_header_invalid",
                )
            if not isinstance(binding, MCPContextHeaderBinding):
                raise DefinitionError(
                    "MCP context header values must be MCPContextHeaderBinding instances.",
                    code="mcp_context_header_invalid",
                    details={"header": name},
                )
            folded = name.casefold()
            if folded in folded_names:
                raise DefinitionError(
                    "MCP context header names must be unique case-insensitively.",
                    code="mcp_context_header_conflict",
                    details={"header": name},
                )
            folded_names.add(folded)
            normalized[name] = binding
        object.__setattr__(self, "headers", MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class MCPContextHeaders:
    """Resolve declarative MCP header bindings from one trusted Agent context."""

    configuration: MCPContextHeadersConfig

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, MCPContextHeadersConfig):
            raise TypeError("configuration must be MCPContextHeadersConfig")

    def __call__(self, context: AgentContext) -> Mapping[str, str]:
        resolved: dict[str, str] = {}
        for header, binding in self.configuration.headers.items():
            value = _resolve_source(context, binding.source)
            if value is None:
                if binding.required:
                    raise RunError(
                        f"Required MCP context header source {binding.source!r} is missing.",
                        code="mcp_context_header_missing",
                        details={"header": header, "source": binding.source},
                    )
                continue
            resolved[header] = _header_value(value)
        return resolved


@dataclass(frozen=True, slots=True)
class _MCPRecipe:
    url: str
    capability_id: str
    native: bool
    local: bool | None
    authorization_token: str | None
    static_headers: tuple[tuple[str, str], ...]
    allowed_tools: tuple[str, ...] | None
    description: str | None
    defer_loading: bool


class ContextualMCP(MCP[AgentContext]):
    """Build one fresh upstream MCP capability with run-resolved headers."""

    def __init__(
        self,
        url: str,
        *,
        id: str,
        headers_factory: MCPHeadersFactory,
        native: bool = False,
        local: bool | None = None,
        authorization_token: str | None = None,
        headers: Mapping[str, str] | None = None,
        allowed_tools: list[str] | tuple[str, ...] | None = None,
        description: str | None = None,
        defer_loading: bool = False,
    ) -> None:
        if not isinstance(url, str) or not url.strip():
            raise DefinitionError("Contextual MCP url must be a non-blank string.", code="mcp_definition_invalid")
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise DefinitionError("Contextual MCP url must be an HTTP(S) URL.", code="mcp_definition_invalid")
        if not isinstance(id, str) or not id.strip() or ":" in id:
            raise DefinitionError(
                "Contextual MCP id must be a non-blank string without ':'.",
                code="mcp_definition_invalid",
            )
        if not callable(headers_factory):
            raise DefinitionError("Contextual MCP headers_factory must be callable.", code="mcp_definition_invalid")
        if not isinstance(native, bool) or (local is not None and not isinstance(local, bool)):
            raise DefinitionError(
                "Contextual MCP supports only boolean URL-based native/local selection.",
                code="mcp_definition_invalid",
            )
        if native is False and local is False:
            raise DefinitionError(
                "Contextual MCP native and local execution cannot both be disabled.",
                code="mcp_definition_invalid",
            )

        static_headers = dict(headers or {})
        _require_header_mapping(static_headers, source="static")
        recipe = _MCPRecipe(
            url=url,
            capability_id=id,
            native=native,
            local=local,
            authorization_token=authorization_token,
            static_headers=tuple(static_headers.items()),
            allowed_tools=tuple(allowed_tools) if allowed_tools is not None else None,
            description=description,
            defer_loading=defer_loading,
        )
        self._recipe = recipe
        self._headers_factory = headers_factory

        # The shared definition is intentionally inert. Its fresh upstream replacement is
        # constructed in for_run(), before Pydantic AI re-extracts MCP tools and Toolsets.
        self.url = url
        self.id = id
        self.native = False
        self.local = None
        self.authorization_token = None
        self.headers = None
        self.allowed_tools = None
        self.description = description
        self.defer_loading = defer_loading

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        cache_id = f"a13n.mcp.context.{self._recipe.capability_id}"
        existing = ctx.deps._run_capability(cache_id)
        if existing is not None:
            if not isinstance(existing, MCP):
                raise DefinitionError(
                    "Contextual MCP has an incompatible logical-run replacement.",
                    code="capability_type_mismatch",
                )
            return existing

        produced = self._headers_factory(ctx.deps)
        if inspect.isawaitable(produced):
            produced = await produced
        if not isinstance(produced, Mapping):
            raise TypeError("MCP headers_factory must return a mapping")
        dynamic_headers = dict(cast(Mapping[object, object], produced))
        _require_header_mapping(dynamic_headers, source="resolved")
        validated_dynamic_headers = cast(dict[str, str], dynamic_headers)
        static_headers = dict(self._recipe.static_headers)
        _require_no_header_conflicts(static_headers, validated_dynamic_headers)
        merged_headers = {**static_headers, **validated_dynamic_headers}

        replacement = MCP[AgentContext](
            self._recipe.url,
            id=self._recipe.capability_id,
            native=self._recipe.native,
            local=self._recipe.local,
            authorization_token=self._recipe.authorization_token,
            headers=merged_headers or None,
            allowed_tools=list(self._recipe.allowed_tools) if self._recipe.allowed_tools is not None else None,
            description=self._recipe.description,
            defer_loading=self._recipe.defer_loading,
        )
        ctx.deps._record_run_capability(cache_id, replacement)
        return replacement


def _is_supported_source(source: str) -> bool:
    if source in {
        "identity.issuer",
        "identity.subject",
        "instance.agent_instance_id",
        "instance.parent_agent_instance_id",
        "instance.delegation_id",
        "instance.actor",
        "context.run_id",
        "context.thread_id",
    }:
        return True
    if source.startswith("identity."):
        return bool(source.removeprefix("identity."))
    if source.startswith("context.metadata."):
        return bool(source.removeprefix("context.metadata."))
    return False


def _resolve_source(context: AgentContext, source: str) -> JsonValue | str | None:
    if source == "identity.issuer":
        return context.instance.identity.issuer
    if source == "identity.subject":
        return context.instance.identity.subject
    if source.startswith("identity."):
        return context.instance.identity.get_claim(source.removeprefix("identity."))
    if source == "instance.agent_instance_id":
        return context.instance.agent_instance_id
    if source == "instance.parent_agent_instance_id":
        return context.instance.parent_agent_instance_id
    if source == "instance.delegation_id":
        return context.instance.delegation_id
    if source == "instance.actor":
        return context.instance.actor
    if source == "context.run_id":
        return context.run_id
    if source == "context.thread_id":
        return context.thread_id
    metadata_key = source.removeprefix("context.metadata.")
    return context.metadata.get(metadata_key)


def _header_value(value: JsonValue | str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float | dict | list):
        return dump_json_text(value, sort_keys=True)
    raise TypeError(f"Unsupported MCP context header value: {type(value).__name__}")


def _require_header_mapping(headers: Mapping[Any, Any], *, source: str) -> None:
    folded_names: set[str] = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(value, str):
            raise TypeError(f"MCP {source} headers must map non-blank strings to strings")
        folded = name.casefold()
        if folded in folded_names:
            raise DefinitionError(
                f"MCP {source} header names must be unique case-insensitively.",
                code="mcp_context_header_conflict",
                details={"header": name},
            )
        folded_names.add(folded)


def _require_no_header_conflicts(static: Mapping[str, str], dynamic: Mapping[str, str]) -> None:
    static_names = {name.casefold() for name in static}
    for name in dynamic:
        if name.casefold() in static_names:
            raise DefinitionError(
                "Static and resolved MCP header names must not overlap case-insensitively.",
                code="mcp_context_header_conflict",
                details={"header": name},
            )


__all__ = [
    "ContextualMCP",
    "MCPContextHeaderBinding",
    "MCPContextHeaders",
    "MCPContextHeadersConfig",
    "MCPHeadersFactory",
]
