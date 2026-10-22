"""Toolset-based progressive presentation without source regrouping or rebinding."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256

from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ToolSearch
from pydantic_ai.messages import InstructionPart, ModelRequest, TextContent, ToolsetInstructionSource, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.native_tools import AbstractNativeTool
from pydantic_ai.toolsets import AbstractToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.exposure import EXPOSURE_CAPABILITY_ID, ToolExposure, active_tools, is_progressive
from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability
from a13n_harness.tools.surface import ToolSurfaceCapability
from a13n_harness.toolsets.progressive import ProgressiveToolset, directory


class _DirectoryState(BaseModel):
    digest: str | None = None


class ToolExposureCapability(AbstractCapability[AgentContext]):
    """Apply one captured policy to the complete effective native tool directory."""

    id = EXPOSURE_CAPABILITY_ID

    def __init__(self, policy: ToolExposure) -> None:
        self.policy = policy.model_copy(deep=True)
        self.instructions: dict[str, list[str]] = {}
        self._directory_state = _DirectoryState()
        self._directory_delivered = False

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(EXPOSURE_CAPABILITY_ID)
        if existing is not None:
            return existing
        owner = ToolExposureCapability(self.policy)
        owner._directory_state = (
            await ctx.deps.state.read(EXPOSURE_CAPABILITY_ID, _DirectoryState, version="1") or _DirectoryState()
        )
        ctx.deps._record_run_capability(EXPOSURE_CAPABILITY_ID, owner)
        return owner

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.capabilities.codeact import CodeActCapability

        return CapabilityOrdering(
            position="outermost",
            wraps=(ToolSurfaceCapability,),
            wrapped_by=(ToolExecutionBoundaryCapability, CodeActCapability),
        )

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return ProgressiveToolset(toolset, self.policy)

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        parameters = request_context.model_request_parameters
        tools = active_tools(ctx)
        if self.policy.progressive_search == "harness":
            progressive_sets = {tool.tool_def.toolset_id for tool in tools.values() if is_progressive(tool.tool_def)}
            direct_sets = {tool.tool_def.toolset_id for tool in tools.values() if not is_progressive(tool.tool_def)}
            hidden_sets = progressive_sets - direct_sets
            instructions: list[InstructionPart] = []
            self.instructions = {}
            for part in parameters.instruction_parts or []:
                source = part.id.source if part.id is not None else None
                if isinstance(source, ToolsetInstructionSource) and source.id in hidden_sets:
                    self.instructions.setdefault(source.id, []).append(part.content)
                else:
                    instructions.append(part)
            if progressive_sets:
                instructions.append(
                    InstructionPart(
                        content=(
                            "Additional tools use progressive discovery. Browse list_toolsets/list_tools, "
                            "search_tools for keywords, describe_tools for full definitions, and execute_tool "
                            "for invocation. Known names can be invoked without prior discovery."
                        )
                    )
                )
            request_context.model_request_parameters = replace(
                parameters,
                function_tools=[tool for tool in parameters.function_tools if not is_progressive(tool)],
                instruction_parts=instructions,
            )
        if not self._directory_delivered:
            self._directory_delivered = True
            reminder = directory(tools, self.policy)
            inventory = sorted(
                (tool.tool_def.toolset_id, tool.tool_def.name)
                for tool in tools.values()
                if is_progressive(tool.tool_def)
            )
            digest = sha256(json.dumps([reminder, inventory], ensure_ascii=False).encode()).hexdigest()
            # An initially empty directory has nothing to advertise. A transition
            # to empty still replaces a previously delivered directory.
            any_progressive = any(is_progressive(tool.tool_def) for tool in tools.values())
            if digest != self._directory_state.digest and (any_progressive or self._directory_state.digest is not None):
                message = directory_message(reminder)
                ctx.messages.append(message)
                request_context.messages = [*request_context.messages, message]
                self._directory_state.digest = digest
                await ctx.deps.state.write(EXPOSURE_CAPABILITY_ID, self._directory_state, version="1")
        return request_context


def directory_message(content: str) -> ModelRequest:
    return ModelRequest(
        parts=[UserPromptPart([TextContent(content, metadata={"display": False, "source_id": EXPOSURE_CAPABILITY_ID})])]
    )


class RequireNativeToolSearch(AbstractCapability[AgentContext]):
    """Require the provider path rather than accepting upstream local fallback."""

    def __init__(self, search: ToolSearch[AgentContext]) -> None:
        self.search = search

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        supported = tuple(request_context.model.profile.get("supported_native_tools", ()))
        for tool in self.search.get_native_tools():
            if isinstance(tool, AbstractNativeTool) and not isinstance(tool, supported):
                raise DefinitionError(
                    "progressive_search='native' requires a model supporting native tool search.",
                    code="native_tool_search_unsupported",
                )
        parameters = request_context.model_request_parameters
        request_context.model_request_parameters = replace(
            parameters,
            native_tools=[
                replace(tool, optional=False) if tool.kind == "tool_search" else tool
                for tool in parameters.native_tools
            ],
            function_tools=[tool for tool in parameters.function_tools if tool.unless_native != "tool_search"],
        )
        return request_context
