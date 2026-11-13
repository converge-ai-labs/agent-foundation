"""Object capture, management, and opt-in tool argument transport."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset, ToolsetTool, WrapperToolset

from a13n_harness.capabilities._object_ref_validation import ReferenceValidator
from a13n_harness.context import AgentContext
from a13n_harness.objects import (
    OBJECT_REF_MANAGEMENT,
    OBJECT_STORE_CAPABILITY_ID,
    SUPPORTS_OBJECT_REFS,
    ObjectInfo,
    ObjectStore,
    ObjectStoreError,
    contains_object_ref,
    is_object_ref,
    object_id,
)
from a13n_harness.tools._deferred_dispatch import FORWARDED_TOOL_KEY
from a13n_harness.tools.diagnostics import system_failure

_REF_SCHEMA = {"type": "string", "pattern": r"^a13n-object://obj_[a-zA-Z0-9_-]+$"}


class ObjectStoreConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capture_media: bool = True
    max_object_bytes: int = Field(default=100 * 1024 * 1024, gt=0, le=512 * 1024 * 1024)


def is_object_ref_target(tool_def: Any) -> bool:
    metadata = tool_def.metadata or {}
    return not (
        metadata.get(OBJECT_REF_MANAGEMENT) or metadata.get("a13n.codeact.runner") or FORWARDED_TOOL_KEY in metadata
    )


def check_object_ref_support(ctx: RunContext[AgentContext], tool_def: Any, args: object) -> None:
    """Diagnostic recognition is available even without the optional feature."""
    if not is_object_ref_target(tool_def) or not contains_object_ref(args):
        return
    if ctx.deps.object_store is None or OBJECT_STORE_CAPABILITY_ID not in ctx.capabilities:
        raise system_failure(
            "object_refs_disabled",
            "Object references are disabled. Developer: enable ObjectStoreCapability, bind RunBindings.object_store, "
            "and opt in the intended tool/toolset/MCP server with supports_object_refs. "
            "Do not invent base64 or change deployment configuration from the Agent.",
        )
    if (tool_def.metadata or {}).get(SUPPORTS_OBJECT_REFS) is not True:
        raise system_failure(
            "object_refs_not_supported",
            f"Tool {tool_def.name!r} does not support object references. Developer: opt in this tool, "
            "toolset, or MCP server with supports_object_refs when its string arguments accept base64.",
        )


@dataclass
class ObjectRefToolset(WrapperToolset[AgentContext]):
    """Opt tools into Object Reference Transport, with per-tool metadata overrides."""

    supports_object_refs: bool = True
    tool_overrides: dict[str, bool] = field(default_factory=dict)

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: replace(
                tool,
                tool_def=replace(
                    tool.tool_def,
                    metadata={
                        SUPPORTS_OBJECT_REFS: self.supports_object_refs,
                        **(tool.tool_def.metadata or {}),
                        **({SUPPORTS_OBJECT_REFS: self.tool_overrides[name]} if name in self.tool_overrides else {}),
                    },
                ),
            )
            for name, tool in tools.items()
        }


def _reference_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_reference_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"properties", "$defs", "definitions", "patternProperties"}:
            result[key] = {name: _reference_schema(schema) for name, schema in item.items()}
        elif key in {"items", "additionalProperties", "anyOf", "oneOf", "allOf", "prefixItems"}:
            result[key] = _reference_schema(item)
        else:
            result[key] = deepcopy(item)
    # Keep unconstrained strings unchanged; constrained strings also accept an exact ref.
    if result.get("type") == "string" and any(
        key in result for key in ("pattern", "format", "contentEncoding", "minLength", "maxLength", "enum", "const")
    ):
        return {"anyOf": [result, dict(_REF_SCHEMA)]}
    return result


@dataclass
class _ReferenceValidator:
    original: ToolsetTool[AgentContext]
    transport: ReferenceValidator

    def validate_python(self, value: Any, **kwargs: Any) -> Any:
        return self.transport.validate_python(value, **kwargs)

    def validate_json(self, value: Any, **kwargs: Any) -> Any:
        return self.transport.validate_json(value, **kwargs)


def _prepare_reference_tool(tool: ToolsetTool[AgentContext]) -> ToolsetTool[AgentContext]:
    definition = replace(
        tool.tool_def,
        parameters_json_schema=_reference_schema(tool.tool_def.parameters_json_schema),
        description=(tool.tool_def.description or "")
        + "\nObject Reference Transport: accepts a13n-object://obj_ references as whole string values in place of base64. "
        "Runtime supplies original bytes. Use only refs returned by the Object Store; never generate base64.",
    )
    return replace(
        tool,
        tool_def=definition,
        args_validator=_ReferenceValidator(tool, ReferenceValidator.create(tool.args_validator)),
    )


@dataclass
class _ObjectStoreToolset(WrapperToolset[AgentContext]):
    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: _prepare_reference_tool(tool)
            if is_object_ref_target(tool.tool_def) and (tool.tool_def.metadata or {}).get(SUPPORTS_OBJECT_REFS) is True
            else tool
            for name, tool in tools.items()
        }

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if is_object_ref_target(tool.tool_def) and contains_object_ref(tool_args):
            check_object_ref_support(ctx, tool.tool_def, tool_args)
            store = ctx.deps.object_store
            assert store is not None
            snapshot: dict[str, str] = {}

            async def expand(value: Any) -> Any:
                if not contains_object_ref(value):
                    return value
                if isinstance(value, str) and is_object_ref(value):
                    if value not in snapshot:
                        snapshot[value] = base64.b64encode(await store.read(object_id(value))).decode("ascii")
                    return snapshot[value]
                if isinstance(value, BaseModel):
                    return value.model_copy(update={key: await expand(item) for key, item in dict(value).items()})
                if isinstance(value, dict):
                    return {key: await expand(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [await expand(item) for item in value]
                if isinstance(value, tuple):
                    # NamedTuple has no distinct base class; preserve its field protocol.
                    if hasattr(value, "_fields"):
                        named = cast(Any, value)
                        return named._replace(
                            **{key: await expand(item) for key, item in zip(named._fields, value, strict=True)}
                        )
                    return tuple([await expand(item) for item in value])
                return value

            try:
                effective = await expand(tool_args)
            except ObjectStoreError as exc:
                raise system_failure(exc.code, str(exc)) from exc
            validator = tool.args_validator
            assert isinstance(validator, _ReferenceValidator)
            # Validation, hooks and policy have already decided these arguments.
            # Object Reference Transport only replaces tokens in the final copy.
            return await self.wrapped.call_tool(name, effective, ctx, validator.original)
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)


@dataclass(init=False)
class ObjectStoreCapability(AbstractCapability[AgentContext]):
    """Reuse original media bytes through a scoped Host Store and opted-in tools."""

    id = OBJECT_STORE_CAPABILITY_ID

    def __init__(self, configuration: ObjectStoreConfiguration | None = None) -> None:
        self.configuration = configuration or ObjectStoreConfiguration()
        self._captured: dict[tuple[str, str], ObjectInfo] = {}

    async def for_run(self, ctx: RunContext[AgentContext]) -> ObjectStoreCapability:
        return ObjectStoreCapability(self.configuration)

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability

        return CapabilityOrdering(wrapped_by=(ToolExecutionBoundaryCapability,))

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return _ObjectStoreToolset(toolset)

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        from a13n_harness.toolsets.object_store import ObjectStoreTools

        owner = ObjectStoreTools(self.configuration)
        tools = [owner.asset_ref, owner.asset_store, owner.asset_list, owner.asset_info, owner.asset_delete]
        return FunctionToolset(
            tools=[Tool(function, metadata={OBJECT_REF_MANAGEMENT: True}) for function in tools],
            id="a13n-object-store-tools",
        )

    async def _capture(self, store: ObjectStore, content: Any) -> list[str]:
        values = content if isinstance(content, list | tuple) else [content]
        refs = []
        for value in values:
            if not isinstance(value, BinaryContent) or len(value.data) > self.configuration.max_object_bytes:
                continue
            key = value.media_type, sha256(value.data).hexdigest()
            cached = self._captured.get(key)
            if cached is not None:
                try:
                    await store.info(cached.object_id)
                except ObjectStoreError as error:
                    if error.code != "object_ref_not_found":
                        raise
                    self._captured.pop(key)
            if key not in self._captured:
                try:
                    self._captured[key] = await store.put(value.data, media_type=value.media_type)
                except ObjectStoreError as error:
                    if error.code != "object_too_large":
                        raise
                    # Capture is additive: a Host's smaller storage budget must
                    # not discard native media or fail an otherwise valid Run.
                    refs.append(json.dumps({"error": error.code, "message": str(error)}))
                    continue
            refs.append(json.dumps(self._captured[key].presentation()))
        return refs

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if not self.configuration.capture_media:
            return request_context
        store = ctx.deps.object_store
        assert store is not None
        for message in request_context.messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if (
                        isinstance(part, UserPromptPart)
                        and not isinstance(part.content, str)
                        and not any(
                            isinstance(item, str) and item.startswith("Object Store: ") for item in part.content
                        )
                    ):
                        refs = await self._capture(store, part.content)
                        # Add beside, never replace, native vision content. Marker
                        # prevents duplicate text on repeated requests in this Run.
                        additions = [f"Object Store: {ref}" for ref in refs]
                        part.content = [*part.content, *(item for item in additions if item not in part.content)]
        return request_context

    async def after_tool_execute(
        self, ctx: RunContext[AgentContext], *, call: Any, tool_def: Any, args: Any, result: Any
    ) -> Any:
        if not self.configuration.capture_media or not is_object_ref_target(tool_def):
            return result
        store = ctx.deps.object_store
        assert store is not None
        refs = await self._capture(store, result.return_value if isinstance(result, ToolReturn) else result)
        if isinstance(result, ToolReturn):
            refs.extend(await self._capture(store, result.content))
        if not refs:
            return result
        additions = [f"Object Store: {ref}" for ref in dict.fromkeys(refs)]
        if isinstance(result, ToolReturn):
            content = result.content
            values: list[Any] = (
                list(content) if isinstance(content, list | tuple) else ([content] if content is not None else [])
            )
            return replace(result, content=[*values, *additions])
        # Native media lists must stay flat: nesting them in return_value
        # makes native adapters serialize the images as text/base64.
        return [*(result if isinstance(result, list | tuple) else [result]), *additions]


__all__ = ["ObjectRefToolset", "ObjectStoreCapability", "ObjectStoreConfiguration"]
