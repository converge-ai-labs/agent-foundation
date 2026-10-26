"""Grouped discovery over ordinary Toolsets, with native nested execution."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, Tool
from pydantic_ai.exceptions import (
    ApprovalRequired,
    CallDeferred,
    ToolFailed,
    ToolFailedError,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.messages import InstructionPart
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import EnvironmentActivationError
from a13n_harness.tools._deferred_dispatch import FORWARDED_TOOL_KEY, ToolCallForwarder, forward_tool_call
from a13n_harness.tools.tool_search import (
    PROXY_CONTROL_KEY,
    PROXY_MEMBERSHIP_KEY,
    ProxyToolSearchConfig,
    ToolSearchMembership,
    active_proxy_tools,
    grouped_tool_name,
    proxy_control,
    proxy_directory,
    proxy_membership,
    resolve_proxy_call,
    resolve_proxy_tool,
    validate_group,
    validate_proxy_target,
)
from a13n_harness.toolsets._instructions import tool_instruction
from a13n_harness.toolsets.codeact import (
    CodeActPolicyToolset,
    CodeActSourceToolCarrier,
    CodeActToolPolicy,
    is_codeact_tool_eligible,
    resolve_codeact_eligibility,
)


@dataclass(kw_only=True)
class _GroupTool(ToolsetTool[AgentContext]):
    source_tool: ToolsetTool[AgentContext]
    source_toolset: AbstractToolset[AgentContext]
    owner_local_name: str
    codeact_eligible: bool


@dataclass
class _GroupedToolset(WrapperToolset[AgentContext]):
    """Assign an arbitrary local Toolset to a flat ToolSearch group.

    Compose with ToolSearchCapability. Native lifecycle and dispatch stay on the
    wrapped Toolset; only names and model-facing discovery are changed.
    """

    group: str
    group_description: str
    source_label: str

    @property
    def label(self) -> str:
        return f"ToolSearch group {self.group!r} source {self.source_label!r}"

    @property
    def tool_name_conflict_hint(self) -> str:
        return "Give the source tools distinct names or select separate ToolSearch groups."

    def __post_init__(self) -> None:
        validate_group(self.group, self.group_description)

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        result: dict[str, ToolsetTool[AgentContext]] = {}
        for name, tool in (await self.wrapped.get_tools(ctx)).items():
            from a13n_harness.tools.identity import identify_tool

            tool = identify_tool(tool)
            definition = tool.tool_def
            validate_proxy_target(definition)
            if proxy_membership(definition) is not None:
                raise UserError(f"Tool {name!r} already belongs to a ToolSearch group; wrap each source only once.")
            canonical = grouped_tool_name(self.group, name)
            metadata = dict(definition.metadata or {})
            if definition.kind == "external":
                from a13n_harness.tools.client import CLIENT_TOOL_MARKER_KEY

                metadata.setdefault(
                    CLIENT_TOOL_MARKER_KEY,
                    {"declared_name": name, "toolset_id": definition.toolset_id or tool.toolset.id},
                )
            result[canonical] = _GroupTool(
                toolset=self,
                tool_def=replace(
                    definition,
                    name=canonical,
                    metadata={
                        **metadata,
                        PROXY_MEMBERSHIP_KEY: ToolSearchMembership(self.group, self.group_description, name),
                    },
                ),
                max_retries=tool.max_retries,
                args_validator=tool.args_validator,
                args_validator_func=tool.args_validator_func,
                source_tool=tool,
                source_toolset=self.wrapped,
                owner_local_name=name,
                codeact_eligible=resolve_codeact_eligibility(name, tool),
            )
        return result

    async def get_instructions(
        self, ctx: RunContext[AgentContext]
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        # Proxy source guidance is returned with discovery, not eagerly for every tool.
        return None

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if not isinstance(tool, _GroupTool):
            raise TypeError("_GroupedToolset received a tool it does not own")
        source = tool.source_tool
        local_ctx = replace(ctx, tool_name=tool.owner_local_name)
        return await self.wrapped.call_tool(tool.owner_local_name, tool_args, local_ctx, source)


@dataclass
class ToolSearchSurfaceToolset(WrapperToolset[AgentContext]):
    """Contribute the proxy controls after mandatory surface resolution."""

    config: ProxyToolSearchConfig

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        directory = proxy_directory(tools)
        if not directory:
            return tools
        for name in (
            self.config.search_name,
            self.config.describe_name,
            self.config.call_name,
            self.config.list_name,
            self.config.groups_name,
        ):
            if name in tools:
                raise UserError(
                    f"ToolSearch entry name {name!r} conflicts with an existing tool; configure another name"
                )
        search = Tool(
            self._search,
            name=self.config.search_name,
            description=(
                "Discover tools without executing them. Any keyword can match names, descriptions, groups, or input parameters; use short keywords, "
                f"or an exact tool name. Omit group to search all groups; use {self.config.list_name} to browse. "
                "Exact names rank first without hiding other matches. Auto expands only a sole exact candidate with a small definition. "
                f"For summaries, use {self.config.describe_name} before {self.config.call_name} unless the schema "
                "is already known. Pagination applies to the current directory."
            ),
            metadata={PROXY_CONTROL_KEY: "search"},
        )
        describe = Tool(
            self._describe,
            name=self.config.describe_name,
            description=(
                "Get one tool's complete current argument schema and instructions without executing it. "
                "Supply its exact group and local tool name; prior discovery is not required. "
                f"Then use {self.config.call_name} with matching arguments."
            ),
            metadata={PROXY_CONTROL_KEY: "describe"},
        )
        call = Tool(
            self._call,
            name=self.config.call_name,
            description=(
                f"Execute one tool discovered with {self.config.search_name}. Copy its exact group and tool name, "
                "and supply an arguments object matching its schema. Already-known current schemas can be reused. "
                "This call can have side effects; after uncertain failure inspect the outcome before retrying."
            ),
            sequential=True,
            metadata={PROXY_CONTROL_KEY: "call", FORWARDED_TOOL_KEY: ToolCallForwarder(resolve_proxy_call)},
        )
        listing = Tool(
            self._list,
            name=self.config.list_name,
            description=(
                "List all available Tool Search tools, optionally within a group, without keyword filtering. "
                "Follow next_offset until null to retrieve the complete current directory. "
                f"Use {self.config.describe_name} for full schemas or request detail='full'."
            ),
            metadata={PROXY_CONTROL_KEY: "list"},
        )
        groups = Tool(
            self._groups,
            name=self.config.groups_name,
            description=(
                "List available Tool Search groups, their purposes and tool counts. "
                f"Follow next_offset until null; use {self.config.list_name} to browse their tools."
            ),
            metadata={PROXY_CONTROL_KEY: "groups"},
        )
        controls = await CodeActPolicyToolset(
            FunctionToolset([search, describe, call, listing, groups], id="tool-search-controls"),
            CodeActToolPolicy(default=True),
        ).get_tools(ctx)
        # Inventory belongs in tool results, never in cached control definitions.
        for name in (self.config.search_name, self.config.list_name):
            prepared = controls[name]
            schema = deepcopy(prepared.tool_def.parameters_json_schema)
            schema["properties"]["limit"]["anyOf"][0]["maximum"] = self.config.max_results
            controls[name] = replace(prepared, tool_def=replace(prepared.tool_def, parameters_json_schema=schema))
        return {**tools, **controls}

    async def get_instructions(
        self, ctx: RunContext[AgentContext]
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        wrapped = await self.wrapped.get_instructions(ctx)
        if not ctx.deps.toolset_instructions or ctx.tool_manager is None or not ctx.tool_manager.tools:
            return wrapped
        if not proxy_directory(ctx.tool_manager.tools):
            return wrapped
        parts: list[str | InstructionPart] = []
        if isinstance(wrapped, str | InstructionPart):
            parts.append(wrapped)
        elif wrapped is not None:
            parts.extend(wrapped)
        parts.append(
            tool_instruction("tool_search").format(
                search_name=self.config.search_name,
                describe_name=self.config.describe_name,
                call_name=self.config.call_name,
                list_name=self.config.list_name,
                groups_name=self.config.groups_name,
            )
        )
        return parts

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        role = proxy_control(tool.tool_def)
        if role == "list":
            return await self._list(ctx, **tool_args)
        if role == "groups":
            return await self._groups(ctx, **tool_args)
        if role == "search":
            return await self._search(ctx, **tool_args)
        if role == "describe":
            return await self._describe(ctx, **tool_args)
        if role == "call":
            return await self._call(ctx, **tool_args)
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)

    async def _search(
        self,
        ctx: RunContext[AgentContext],
        query: Annotated[
            str,
            Field(
                max_length=2048,
                description="Any keyword can match; exact names rank first, then keyword coverage. Empty to browse.",
            ),
        ],
        group: Annotated[str | None, Field(description="Optional group; omit to search across groups.")] = None,
        detail: Annotated[
            Literal["auto", "summary", "full"],
            Field(
                description="Auto expands only when the entire candidate set is one exact match with a small definition; summary browses cheaply; full includes complete schemas."
            ),
        ] = "auto",
        limit: Annotated[
            int | None,
            Field(
                ge=1,
                le=100,
                description="Maximum matches; null uses up to 10 summaries or 5 full definitions, within the configured cap.",
            ),
        ] = None,
        offset: Annotated[int, Field(ge=0, description="Offset into this query's current-step matches.")] = 0,
    ) -> dict[str, Any]:
        tools = active_proxy_tools(ctx)
        directory = proxy_directory(tools)
        if group is not None and not any(key[0] == group for key in directory):
            raise ModelRetry(
                f"Group {group!r} is unavailable in this step. Choose a current group or omit group to search all.\n{_group_summary(tools)}"
            )
        if limit is not None and limit > self.config.max_results:
            raise ModelRetry(f"limit must not exceed {self.config.max_results}")
        matches, exact = _search_matches(tools, directory, query, group)
        if detail == "auto":
            if exact and len(matches) == 1 and offset == 0:
                entry = await proxy_discovery_entry(ctx, matches[0])
                expanded = {"detail": "full", "tools": [entry], "total": 1, "next_offset": None}
                if _json_bytes(expanded) <= min(_AUTO_EXPAND_BYTES, self.config.max_search_bytes):
                    return expanded
            detail = "summary"
        if limit is None:
            limit = min(10 if detail == "summary" else 5, self.config.max_results)
        entries: list[dict[str, Any]] = []
        result: dict[str, Any] = {"detail": detail, "tools": entries, "total": len(matches), "next_offset": None}
        for index, prepared in enumerate(matches[offset : offset + limit], offset):
            entry = _summary_entry(prepared) if detail == "summary" else await proxy_discovery_entry(ctx, prepared)
            entries.append(entry)
            next_offset = index + 1
            result["next_offset"] = next_offset if next_offset < len(matches) else None
            if _json_bytes(result) > self.config.max_search_bytes:
                entries.pop()
                if not entries:
                    raise _entry_too_large(prepared, detail)
                result["next_offset"] = index
                break
        return result

    async def _list(
        self,
        ctx: RunContext[AgentContext],
        group: Annotated[str | None, Field(description="Optional group; omit to list all tools.")] = None,
        detail: Literal["summary", "full"] = "summary",
        limit: Annotated[int | None, Field(ge=1, le=100)] = None,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        return await self._search(ctx, query="", group=group, detail=detail, limit=limit, offset=offset)

    async def _groups(self, ctx: RunContext[AgentContext], offset: Annotated[int, Field(ge=0)] = 0) -> dict[str, Any]:
        groups: dict[str, dict[str, Any]] = {}
        for tool in active_proxy_tools(ctx).values():
            member = proxy_membership(tool.tool_def)
            if member is not None:
                entry = groups.setdefault(
                    member.group,
                    {
                        "group": member.group,
                        "description": member.description,
                        "count": 0,
                    },
                )
                entry["count"] += 1
        entries = [entry for _, entry in sorted(groups.items())]
        page: list[dict[str, Any]] = []
        result = {"groups": page, "total": len(entries), "next_offset": None}
        for index, entry in enumerate(entries[offset : offset + self.config.max_results], offset):
            page.append(entry)
            result["next_offset"] = index + 1 if index + 1 < len(entries) else None
            if _json_bytes(result) > self.config.max_search_bytes:
                page.pop()
                if not page:
                    raise ToolFailed(f"Group {entry['group']!r} exceeds the discovery byte budget.")
                result["next_offset"] = index
                break
        return result

    async def _describe(
        self,
        ctx: RunContext[AgentContext],
        group: Annotated[str, Field(description="Exact current group.")],
        tool: Annotated[str, Field(description="Exact local tool name, not its grouped canonical name.")],
    ) -> dict[str, Any]:
        tools = active_proxy_tools(ctx)
        prepared = tools[resolve_proxy_tool(tools, group, tool)]
        result = {"detail": "full", "tool": await proxy_discovery_entry(ctx, prepared)}
        if _json_bytes(result) > self.config.max_search_bytes:
            raise _entry_too_large(prepared, "full")
        return result

    async def _call(
        self,
        ctx: RunContext[AgentContext],
        group: Annotated[str, Field(description="Exact group from discovery.")],
        tool: Annotated[str, Field(description="Exact local tool name from discovery, not a top-level tool.")],
        arguments: Annotated[
            dict[str, Any], Field(description="Arguments matching the discovered tool's current schema.")
        ],
    ) -> Any:
        tools = active_proxy_tools(ctx)
        name, values = resolve_proxy_call(tools, {"group": group, "tool": tool, "arguments": arguments})
        manager = ctx.tool_manager
        assert manager is not None
        if ctx.usage_limits is not None:
            projected = deepcopy(ctx.usage)
            projected.tool_calls += 2  # This outer envelope and one native target call.
            ctx.usage_limits.check_before_tool_call(projected)
        try:
            result = await forward_tool_call(ctx, name, values)
        except ToolRetryError as exc:
            # Native target retries remain keyed by the original prepared name.
            # Re-correlate only the model-facing message; rethrowing ModelRetry
            # here would also consume the shared envelope's unrelated budget.
            raise ToolRetryError(
                replace(exc.tool_retry, tool_name=ctx.tool_name, tool_call_id=ctx.tool_call_id)
            ) from exc
        except ToolFailedError as exc:
            raise ToolFailedError(
                replace(
                    exc.tool_failed, tool_name=ctx.tool_name or self.config.call_name, tool_call_id=ctx.tool_call_id
                )
            ) from exc
        except (ApprovalRequired, CallDeferred):
            raise
        except (ModelRetry, ToolFailed, UsageLimitExceeded, UnexpectedModelBehavior, EnvironmentActivationError):
            raise
        except Exception as exc:
            raise ToolFailed(
                f"Tool {group}/{tool} failed with {type(exc).__name__}; its outcome may be uncertain. "
                "Inspect external state before retrying a mutation."
            ) from None
        if isinstance(result, ToolDenied):
            raise ToolFailed(f"Tool {group}/{tool} was denied: {result.message}")
        return result


_AUTO_EXPAND_BYTES = 8192
_SUMMARY_DESCRIPTION_CHARS = 256


def _words(value: str) -> list[str]:
    # Split identifier boundaries before case folding, including HTTPServer -> HTTP Server.
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.findall(r"[^\W_]+", value.casefold())


def _search_matches(
    tools: dict[str, ToolsetTool[AgentContext]],
    directory: dict[tuple[str, str], str],
    query: str,
    group: str | None,
) -> tuple[list[ToolsetTool[AgentContext]], bool]:
    """Rank current prepared definitions, without constructing schemas or source guidance."""
    query = query.strip()
    scoped = {key: name for key, name in directory.items() if group is None or key[0] == group}
    exact = {
        name for (_, local_name), name in scoped.items() if query.casefold() in (local_name.casefold(), name.casefold())
    }
    tokens = set(_words(query))
    if not tokens:
        if query and not exact:
            raise ModelRetry("Supply operation keywords or an exact tool name; use an empty query to browse.")
        return [tools[name] for name in sorted(exact if query else scoped.values())], bool(exact)
    matches: list[tuple[int, int, tuple[int, ...], str]] = []
    for (group_name, local_name), canonical in scoped.items():
        definition = tools[canonical].tool_def
        fields = (
            local_name,
            definition.description or "",
            group_name,
            *_parameter_search_text(definition.parameters_json_schema),
        )
        coverage, relevance = _keyword_rank(tokens, fields)
        # Exact identity is independent of tokenization (e.g. getHTTP vs GETHTTP).
        if canonical in exact or coverage:
            matches.append((-int(canonical in exact), -coverage, relevance, canonical))
    matches.sort()
    return [tools[name] for _, _, _, name in matches], bool(exact)


def _keyword_rank(tokens: set[str], fields: tuple[str, ...]) -> tuple[int, tuple[int, ...]]:
    """Count each keyword in its strongest whole-word/substring field bucket."""
    best: dict[str, int] = {}
    for field, text in enumerate(fields):
        folded = text.casefold()
        hits = {token for token in tokens if token in folded}
        if not hits:
            continue
        words = set(_words(text))
        for token in hits:
            bucket = field if token in words else len(fields) + field
            best[token] = min(best.get(token, bucket), bucket)
    counts = [0] * (2 * len(fields))
    for bucket in best.values():
        counts[bucket] -= 1
    return len(best), tuple(counts)


def _parameter_search_text(schema: dict[str, Any]) -> tuple[str, str]:
    """Read input property names/descriptions at physical schema nodes, without resolving refs."""
    names: list[str] = []
    descriptions: list[str] = []
    pending = [schema]
    while pending:
        node = pending.pop()
        description = node.get("description")
        if isinstance(description, str):
            descriptions.append(description)
        for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            children = node.get(key)
            if isinstance(children, dict):
                if key == "properties":
                    names.extend(children)
                pending.extend(child for child in children.values() if isinstance(child, dict))
        for key in (
            "items",
            "additionalItems",
            "additionalProperties",
            "contains",
            "propertyNames",
            "unevaluatedItems",
            "unevaluatedProperties",
            "not",
            "if",
            "then",
            "else",
            "allOf",
            "anyOf",
            "oneOf",
            "prefixItems",
        ):
            child = node.get(key)
            if isinstance(child, dict):
                pending.append(child)
            elif isinstance(child, list):
                pending.extend(item for item in child if isinstance(item, dict))
    return " ".join(names), " ".join(descriptions)


def _summary_entry(tool: ToolsetTool[AgentContext]) -> dict[str, Any]:
    member = proxy_membership(tool.tool_def)
    assert member is not None
    description = " ".join((tool.tool_def.description or "").split())
    if len(description) > _SUMMARY_DESCRIPTION_CHARS:
        description = description[: _SUMMARY_DESCRIPTION_CHARS - 1] + "…"
    return {
        "group": member.group,
        "tool": member.tool,
        "description": description,
        "codeact_eligible": is_codeact_tool_eligible(tool.tool_def.name, tool),
    }


def _json_bytes(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _entry_too_large(tool: ToolsetTool[AgentContext], detail: str) -> ToolFailed:
    member = proxy_membership(tool.tool_def)
    assert member is not None
    return ToolFailed(
        f"The complete {detail} entry for {member.group}/{member.tool} exceeds the discovery response budget. "
        "Ask the Host to expose this tool directly or reduce its definition; no partial schema was returned."
    )


def _group_summary(tools: dict[str, ToolsetTool[AgentContext]]) -> str:
    groups = {
        member.group: member.description
        for tool in tools.values()
        if (member := proxy_membership(tool.tool_def)) is not None
    }
    return "Available groups:\n" + "\n".join(f"- {name}: {description}" for name, description in sorted(groups.items()))


async def proxy_discovery_entry(ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]) -> dict[str, Any]:
    """The complete current proxy schema and source guidance, shared with retention."""
    member = proxy_membership(tool.tool_def)
    assert member is not None
    return {
        "group": member.group,
        "tool": member.tool,
        "description": tool.tool_def.description,
        "parameters_json_schema": deepcopy(tool.tool_def.parameters_json_schema),
        "return_schema": deepcopy(tool.tool_def.return_schema),
        "codeact_eligible": is_codeact_tool_eligible(tool.tool_def.name, tool),
        "instructions": await _source_instructions(ctx, tool),
    }


async def _source_instructions(ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]) -> list[str]:
    if not ctx.deps.toolset_instructions:
        return []
    current = tool
    while isinstance(current, CodeActSourceToolCarrier):
        if isinstance(current, _GroupTool):
            value = await current.source_toolset.get_instructions(ctx)
            if value is None:
                return []
            if isinstance(value, str | InstructionPart):
                return [value.content if isinstance(value, InstructionPart) else value]
            return [part.content if isinstance(part, InstructionPart) else part for part in value]
        current = current.source_tool
    return []
