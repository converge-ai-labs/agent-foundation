"""Capability-owned lifecycle and composition for restricted CodeAct."""

from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.toolsets import AbstractToolset

from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.runtime import CodeActRunState
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability
from a13n_harness.toolsets.codeact import CodeActToolset, render_codeact_runner_description

CODEACT_CAPABILITY_ID = "a13n.codeact"


@dataclass(init=False)
class CodeActCapability(AbstractCapability[AgentContext]):
    """Install one logical-run restricted Python Toolset wrapper."""

    id = CODEACT_CAPABILITY_ID

    def __init__(self, config: CodeActConfig | None = None) -> None:
        if config is not None and not isinstance(config, CodeActConfig):
            raise TypeError("CodeActCapability config must be CodeActConfig or None")
        self.config = config or CodeActConfig()
        self._context: AgentContext | None = None
        self._state: CodeActRunState | None = None

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wrapped_by=(ToolExecutionBoundaryCapability,))

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(CODEACT_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, CodeActCapability):
                raise DefinitionError("CodeAct has an incompatible run replacement.", code="capability_type_mismatch")
            existing._require_context(ctx)
            return existing
        replacement = CodeActCapability(self.config)
        replacement._context = ctx.deps
        replacement._state = CodeActRunState(replacement.config)
        ctx.deps._record_run_capability(CODEACT_CAPABILITY_ID, replacement)
        ctx.deps._register_run_cleanup(CODEACT_CAPABILITY_ID, replacement._close)
        return replacement

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        if self._state is None:
            # Construction-time wrapper discovery may occur before for_run. Pydantic
            # rebuilds wrappers from the run replacement before execution.
            return toolset
        return CodeActToolset(wrapped=toolset, config=self.config, state=self._state)

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        self._require_context(ctx)
        manager = ctx.tool_manager
        if manager is None or manager.tools is None:
            return request_context
        projected = []
        for definition in request_context.model_request_parameters.function_tools:
            metadata = definition.metadata or {}
            if metadata.get("a13n.codeact.runner") is not True:
                projected.append(definition)
                continue
            projected.append(
                replace(
                    definition,
                    description=render_codeact_runner_description(
                        manager.tools,
                        program=metadata.get("a13n.codeact.program") is True,
                    ),
                )
            )
        request_context.model_request_parameters = replace(
            request_context.model_request_parameters,
            function_tools=projected,
        )
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        del request_context
        self._require_context(ctx)
        calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
        manager = ctx.tool_manager

        def is_runner(call: ToolCallPart) -> bool:
            if manager is None or manager.tools is None:
                return call.tool_name in {"run_code", "run_program"}
            tool = manager.tools.get(call.tool_name)
            return tool is not None and (tool.tool_def.metadata or {}).get("a13n.codeact.runner") is True

        if any(is_runner(call) for call in calls) and len(calls) != 1:
            raise ModelRetry("run_code or run_program must be the only executable tool call in a model response")
        return response

    async def _close(self) -> None:
        state = self._state
        self._state = None
        if state is not None:
            await state.close()

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if self._context is None:
            raise DefinitionError("CodeAct run state is not bound.", code="capability_scope_invalid")
        if ctx.deps is not self._context:
            raise DefinitionError("CodeAct state cannot cross logical runs.", code="capability_scope_invalid")
        owner = ctx.capabilities.get(CODEACT_CAPABILITY_ID)
        if owner is not self:
            raise DefinitionError(
                "The finalized CodeAct Capability has incompatible identity.", code="capability_scope_invalid"
            )


__all__ = ["CODEACT_CAPABILITY_ID", "CodeActCapability", "CodeActConfig"]
