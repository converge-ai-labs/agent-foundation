"""Host-prepared MCP capabilities and run-scoped upstream composition."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self, cast
from urllib.parse import urlparse

import httpx2
from a13n_logging import get_logger
from anyio import BrokenResourceError, CancelScope, ClosedResourceError, EndOfStream, sleep
from fastmcp import Client
from fastmcp.client.transports import ClientTransport
from mcp import MCPError
from mcp.types import CONNECTION_CLOSED
from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP as NativeMCP
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._json import dump_json_text
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.http import outbound_tls_verify

_TRANSIENT_HTTP_STATUS = {408, 429, 502, 503, 504}
type StartupErrorPolicy = Literal["skip", "raise"]
type ToolsetFactory = Callable[[], Awaitable[MCPToolset[AgentContext]]]
type PrepareToolset = Callable[[RunContext[AgentContext]], Awaitable[ToolsetFactory]]
type ProjectToolset = Callable[[MCPToolset[AgentContext]], NativeMCP[AgentContext]]


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


class ContextualMCP(NativeMCP[AgentContext]):
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
            if not isinstance(existing, NativeMCP):
                raise DefinitionError(
                    "Contextual MCP has an incompatible logical-run replacement.",
                    code="capability_type_mismatch",
                )
            return existing

        ctx.deps.configuration.authorize_url(self._recipe.url)
        if self._recipe.native and ctx.deps.configuration.allowed_hosts is not None:
            raise DefinitionError(
                "Provider-native MCP cannot enforce Run allowed hosts.", code="mcp_configuration_unsupported"
            )

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

        local: bool | MCPToolset[AgentContext] | None = self._recipe.local
        if ctx.deps.configuration.allowed_hosts is not None:

            async def authorize(request: httpx2.Request) -> None:
                ctx.deps.configuration.authorize_url(str(request.url))

            client = httpx2.AsyncClient(
                verify=outbound_tls_verify(), follow_redirects=False, event_hooks={"request": [authorize]}
            )
            local_headers = dict(merged_headers)
            if self._recipe.authorization_token:
                local_headers["Authorization"] = self._recipe.authorization_token
            local = MCPToolset(
                self._recipe.url,
                id=self._recipe.capability_id,
                headers=local_headers or None,
                http_client=client,
                include_instructions=True,
            )
            ctx.deps._register_run_cleanup(cache_id, client.aclose)

        replacement = NativeMCP[AgentContext](
            self._recipe.url,
            id=self._recipe.capability_id,
            native=self._recipe.native,
            local=local,
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


async def raise_initialization_status(response: httpx2.Response) -> None:
    """Preserve transient initialize/discovery HTTP status before SDK conversion."""
    if response.status_code not in _TRANSIENT_HTTP_STATUS or response.request.method != "POST":
        return
    try:
        payload = json.loads(response.request.content)
    except ValueError:
        return
    if isinstance(payload, dict) and payload.get("method") in {"initialize", "server/discover"}:
        response.raise_for_status()


def _transient_initialization(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return all(_transient_initialization(item) for item in error.exceptions)
    if isinstance(error, httpx2.HTTPStatusError):
        return error.response.status_code in _TRANSIENT_HTTP_STATUS
    if isinstance(error, MCPError):
        return error.error.code == 408
    if isinstance(
        error,
        (TimeoutError, ConnectionError, httpx2.TransportError, BrokenResourceError, ClosedResourceError, EndOfStream),
    ):
        return True
    # FastMCP's exception-group flattening can discard the timeout cause.
    if isinstance(error, RuntimeError) and str(error) == "Failed to initialize server session":
        return True
    return (
        isinstance(error, RuntimeError) and error.__cause__ is not None and _transient_initialization(error.__cause__)
    )


class InitializationRetryClient(Client[ClientTransport]):
    """At most three initialization attempts, with 1s/2s cancellable backoff.

    Ready business requests retain upstream semantics and are never replayed by
    this wrapper. Each attempt retains the native client's initialization timeout.
    A delivered session failure is not delivered again during teardown.
    """

    _reported_session_error: tuple[asyncio.Task[None], BaseException] | None = None

    async def _await_with_session_monitoring[T](self, coro: Coroutine[Any, Any, T]) -> T:
        task = self._session_state.session_task
        try:
            return await super()._await_with_session_monitoring(coro)
        except Exception as exc:
            closed = (
                isinstance(exc, MCPError)
                and exc.error.code == CONNECTION_CLOSED
                and exc.error.message == "Connection closed"
            )
            if task is not None:
                if closed and not task.done():
                    # The SDK can wake requests before the failed session finishes
                    # unwinding. Observe its cause without cancelling it, closing
                    # borrowed contexts, or waiting beyond native cleanup policy.
                    await asyncio.wait({task}, timeout=self._disconnect_timeout)
                if task.done() and not task.cancelled():
                    failure = task.exception()
                    if failure is not None and (closed or failure is exc):
                        self._reported_session_error = (task, failure)
                        if failure is not exc:
                            raise failure from exc
            raise

    async def _disconnect(self, force: bool = False) -> None:
        task = self._session_state.session_task
        try:
            await super()._disconnect(force=force)
        except Exception as exc:
            reported = self._reported_session_error
            # Native disconnect re-awaits the same task. Only its already
            # delivered failure is redundant; new/unobserved close errors remain.
            if reported is None or reported[0] is not task or reported[1] is not exc:
                raise
        finally:
            if self._session_state.session_task is None:
                self._reported_session_error = None

    async def __aenter__(self) -> Self:
        for attempt in range(3):
            try:
                return await super().__aenter__()
            except Exception as exc:
                with CancelScope(shield=True):
                    try:
                        await self.close()
                    except Exception as cleanup:
                        if cleanup is not exc and cleanup is not exc.__cause__:
                            # Cleanup failure still stops retry, but the caller's
                            # primary diagnostic remains the initialization error.
                            raise exc from cleanup
                        try:
                            await self.transport.close()
                        except Exception as transport_cleanup:
                            raise exc from transport_cleanup
                if attempt == 2 or not _transient_initialization(exc):
                    raise
                get_logger(__name__).warning(
                    "MCP initialization failed (%s); retrying attempt %s/3", type(exc).__name__, attempt + 2
                )
                await sleep(2**attempt)
        raise AssertionError("unreachable")


class UnavailableMCPToolset(FunctionToolset[AgentContext]):
    """An omitted inventory, retaining its configured tool-name ceiling."""

    def __init__(self, server_id: str, selected_tools: tuple[str, ...] | None) -> None:
        super().__init__(id=server_id)
        self.selected_tools = selected_tools


class _UnavailableMCP(NativeMCP[AgentContext]):
    def __init__(self, server_id: str, selected_tools: tuple[str, ...] | None) -> None:
        super().__init__(id=server_id, native=False, local=UnavailableMCPToolset(server_id, selected_tools))

    def get_instructions(self) -> str:
        # Capability instructions survive ToolSearch deferral of Toolset guidance.
        return (
            f"MCP server {self.id} is unavailable. "
            "Its tools are omitted for this Run. Continue with the remaining capabilities; "
            "do not claim operations on this server succeeded. A later Run can reassess availability."
        )


class MCPServer(NativeMCP[AgentContext]):
    """A Host-prepared local MCP capability with a separate startup error policy.

    ``prepare`` resolves authority, configuration and credentials outside isolation,
    then returns a factory for the native Toolset. Use ``InitializationRetryClient``
    in that factory for bounded connection retries. ``project`` can retain Host-owned
    filtering or MCP Apps hooks; its errors are not classified as startup failure.
    The entered projection is borrowed until logical-Run cleanup, independently
    of an enclosing Host-owned client lifetime.
    """

    def __init__(
        self,
        *,
        id: str,
        prepare: PrepareToolset,
        on_error: StartupErrorPolicy = "raise",
        tools: tuple[str, ...] | None = None,
        project: ProjectToolset | None = None,
    ) -> None:
        if not id or on_error not in {"skip", "raise"}:
            raise DefinitionError("Invalid MCP startup policy.", code="mcp_definition_invalid")
        super().__init__(id=id, native=False, local=FunctionToolset(id=id))
        self._prepare = prepare
        self._on_error = on_error
        self._selected_tools = tools
        self._project = project

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        assert self.id is not None
        cache_id = f"a13n.mcp.startup.{self.id}"
        existing = ctx.deps._run_capability(cache_id)
        if existing is not None:
            if not isinstance(existing, NativeMCP):
                raise DefinitionError(
                    "MCP has an incompatible logical-Run replacement.", code="capability_type_mismatch"
                )
            return existing

        factory = await self._prepare(ctx)
        try:
            toolset = await factory()
            await toolset.__aenter__()
        except Exception as exc:
            if self._on_error == "raise":
                raise
            get_logger(__name__).warning(
                "MCP server skipped for this Run after connection entry failure",
                extra={"server_id": self.id, "error_type": type(exc).__name__},
            )
            replacement = _UnavailableMCP(self.id, self._selected_tools)
        else:
            # Register cleanup before projecting, so a projection error cannot
            # leak an otherwise successfully entered Toolset.
            async def close_projection() -> None:
                await toolset.__aexit__(None, None, None)

            ctx.deps._register_run_cleanup(cache_id, close_projection)
            replacement = (
                self._project(toolset)
                if self._project is not None
                else NativeMCP[AgentContext](
                    id=self.id,
                    native=False,
                    local=toolset,
                    allowed_tools=list(self._selected_tools) if self._selected_tools is not None else None,
                )
            )
        ctx.deps._record_run_capability(cache_id, replacement)
        return replacement


__all__ = [
    "ContextualMCP",
    "InitializationRetryClient",
    "MCPContextHeaderBinding",
    "MCPContextHeaders",
    "MCPContextHeadersConfig",
    "MCPHeadersFactory",
    "MCPServer",
    "PrepareToolset",
    "ProjectToolset",
    "StartupErrorPolicy",
    "ToolsetFactory",
    "UnavailableMCPToolset",
    "raise_initialization_status",
]
