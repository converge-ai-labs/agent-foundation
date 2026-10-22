"""Toolset-keyed presentation policy and current-target resolution."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import ModelRetry, RunContext, ToolDefinition
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness.context import AgentContext

EXPOSURE_CAPABILITY_ID = "a13n.tool-exposure"
PROGRESSIVE_KEY = "a13n.progressive"
TOOLSET_EXPOSURE_KEY = "a13n.toolset.exposure"
BUILTIN_TOOLSET_DESCRIPTIONS: Mapping[str, str] = {
    "a13n-file-tools": "Read, write, and organize files.",
    "a13n-shell-tools": "Run commands and manage processes.",
    "a13n-computer-tools": "Observe and control an authorized desktop.",
    "a13n-web-tool-functions": "Search and retrieve online content.",
    "a13n-memory-file-tools": "Read and maintain mounted file memories.",
    "a13n-memory-record-tools": "Search and maintain mounted record memories.",
    "a13n-user-interaction-tools": "Ask the user structured questions.",
    "a13n-delegation-tools": "Delegate bounded work to subagents.",
    "a13n-async-subagent-tools": "Start and coordinate independently executing subagents.",
    "a13n-handoff-tools": "Summarize work and continue in a fresh context.",
    "a13n-working-state-tools": "Maintain working notes and tasks.",
    "a13n-media-url-functions": "Read images, audio, and video from URLs.",
    "a13n-document-tool-functions": "Read and convert documents.",
    "codeact-state": "Store and load values shared by CodeAct programs.",
    "tool-execution": "Inspect and control retained tool executions.",
}
DISCOVERY_CONTROL_KEY = "a13n.discovery.control"
INVOCATION_TARGET_KEY = "a13n.invocation.target"
CONTROL_NAMES = ("list_toolsets", "list_tools", "search_tools", "describe_tools", "execute_tool")
type Exposure = Literal["direct", "progressive"]
type ProgressiveSearch = Literal["harness", "native"]


class ToolsetExposure(BaseModel):
    """Presentation of one selected Toolset; never selects or authorizes tools."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exposure: Exposure = "direct"
    tool_exposure: dict[str, Exposure] = Field(default_factory=dict)
    description: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("description")
    @classmethod
    def nonblank_description(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Toolset description must be nonblank")
        return value

    def resolve(self, name: str) -> Exposure:
        return self.tool_exposure.get(name, self.exposure)


class ToolExposure(BaseModel):
    """Captured Agent policy keyed by native ToolDefinition.toolset_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    toolsets: dict[str, ToolsetExposure] = Field(default_factory=dict)
    progressive_search: ProgressiveSearch = "harness"
    strategy: Literal["bm25", "regex"] | None = None

    @field_validator("toolsets")
    @classmethod
    def identified_toolsets(cls, value: dict[str, ToolsetExposure]) -> dict[str, ToolsetExposure]:
        if any(not key.strip() for key in value):
            raise ValueError("Tool exposure requires a nonblank native Toolset ID")
        return value

    @model_validator(mode="after")
    def native_strategy(self) -> ToolExposure:
        if self.strategy is not None and self.progressive_search != "native":
            raise ValueError("strategy is only available with progressive_search='native'")
        return self


def toolset_exposure(definition: ToolDefinition, policy: ToolExposure) -> ToolsetExposure:
    """Resolve captured Agent policy over source declarations and built-in purposes."""
    if definition.toolset_id is None:
        return ToolsetExposure()
    selected = policy.toolsets.get(definition.toolset_id)
    if selected is None:
        declared = (definition.metadata or {}).get(TOOLSET_EXPOSURE_KEY)
        selected = ToolsetExposure.model_validate(declared) if declared is not None else ToolsetExposure()
    if selected.description is None:
        return selected.model_copy(update={"description": BUILTIN_TOOLSET_DESCRIPTIONS.get(definition.toolset_id)})
    return selected


def is_progressive(definition: ToolDefinition) -> bool:
    return (definition.metadata or {}).get(PROGRESSIVE_KEY) is True


def discovery_control(definition: ToolDefinition) -> str | None:
    value = (definition.metadata or {}).get(DISCOVERY_CONTROL_KEY)
    return value if isinstance(value, str) else None


def active_tools(ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
    if ctx.tool_manager is None or ctx.tool_manager.tools is None:
        raise RuntimeError("Progressive discovery requires the current prepared ToolManager")
    return ctx.tool_manager.tools


def executable_target(definition: ToolDefinition) -> bool:
    return (
        definition.kind in {"function", "unapproved", "external"}
        and definition.tool_kind is None
        and not definition.unless_native
        and discovery_control(definition) is None
        and (definition.metadata or {}).get("a13n.codeact.runner") is not True
    )


def resolve_tool(tools: Mapping[str, ToolsetTool[AgentContext]], name: str) -> ToolsetTool[AgentContext]:
    tool = tools.get(name)
    if tool is None or not executable_target(tool.tool_def):
        raise ModelRetry(f"Tool {name!r} is unavailable. Browse or search the current tools.")
    return tool


def resolve_tool_call(
    tools: Mapping[str, ToolsetTool[AgentContext]], arguments: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    name, values = arguments.get("name"), arguments.get("arguments")
    if set(arguments) != {"name", "arguments"} or not isinstance(name, str) or not isinstance(values, dict):
        raise ModelRetry("Supply only an exact tool name and an arguments object matching its current schema.")
    return resolve_tool(tools, name).tool_def.name, values


class InvocationTarget(BaseModel):
    """Native deferred metadata associating an outer call with its validated target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    arguments: dict[str, Any]
    kind: Literal["function", "unapproved", "external"]


def invocation_target(call: ToolCallPart, metadata: Mapping[str, Any]) -> InvocationTarget | None:
    value = metadata.get(INVOCATION_TARGET_KEY)
    if call.tool_name != "execute_tool" or value is None:
        return None
    return InvocationTarget.model_validate(value)


def project_tool_call(call: ToolCallPart, metadata: Mapping[str, Any]) -> ToolCallPart:
    """Project the real target for Host display/execution, retaining outer correlation."""
    target = invocation_target(call, metadata)
    if target is None:
        return call
    return ToolCallPart(target.name, target.arguments, tool_call_id=call.tool_call_id)


@dataclass
class TargetValidation:
    """Task-local observation of native validation for one nested invocation."""

    call_id: str
    outer_call_id: str
    arguments: dict[str, Any] | None = None


TARGET_VALIDATION: ContextVar[TargetValidation | None] = ContextVar("a13n_target_validation", default=None)


def capture_target_arguments(call: ToolCallPart, args: dict[str, Any]) -> None:
    capture = TARGET_VALIDATION.get()
    if capture is not None and capture.call_id == call.tool_call_id:
        from a13n_harness._json import project_json

        projected = project_json(args)
        assert isinstance(projected, dict)
        capture.arguments = projected
