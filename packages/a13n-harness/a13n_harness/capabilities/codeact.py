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
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability
from a13n_harness.toolsets.codeact import CodeActToolset, render_codeact_runner_description
from a13n_harness.toolsets.codeact_state import CODEACT_STATE_ID, CodeActStateToolset

CODEACT_CAPABILITY_ID = CODEACT_STATE_ID


@dataclass(init=False)
class CodeActCapability(AbstractModelContextCapability):
    """Install one logical-run restricted Python Toolset wrapper."""

    id = CODEACT_CAPABILITY_ID

    def __init__(self, config: CodeActConfig | None = None) -> None:
        if config is not None and not isinstance(config, CodeActConfig):
            raise TypeError("CodeActCapability config must be CodeActConfig or None")
        self.config = config or CodeActConfig()
        self._context: AgentContext | None = None
        self._state: CodeActRunState | None = None
        self._stored_values: CodeActStateToolset | None = None

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wrapped_by=(ToolExecutionBoundaryCapability,))

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(CODEACT_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, CodeActCapability):
                raise DefinitionError("CodeAct has an incompatible run replacement.", code="capability_type_mismatch")
            # Nested same-agent runs resolve replacements before ctx.capabilities
            # is finalized. Execution hooks still check the finalized identity.
            if existing._context is not ctx.deps:
                raise DefinitionError("CodeAct state cannot cross logical runs.", code="capability_scope_invalid")
            return existing
        replacement = CodeActCapability(self.config)
        replacement._context = ctx.deps
        replacement._state = CodeActRunState(replacement.config)
        replacement._stored_values = CodeActStateToolset(ctx.deps, replacement.config)
        replacement._stored_values.validate(await replacement._stored_values.snapshot())
        ctx.deps._record_run_capability(CODEACT_CAPABILITY_ID, replacement)
        ctx.deps._register_run_cleanup(CODEACT_CAPABILITY_ID, replacement._close)
        return replacement

    def get_toolset(self) -> AbstractToolset[AgentContext] | None:
        return self._stored_values.get_toolset() if self._stored_values is not None else None

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        self._require_context(ctx)
        projection = await handler(request)
        if self._stored_values is None:
            raise RuntimeError("CodeAct stored values require an active run")
        index = await self._stored_values.context_index()
        if not index:
            return projection
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id="codeact.stored_keys",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=index,
                ),
            )
        )

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        if self._state is None:
            # Construction-time wrapper discovery may occur before for_run. Pydantic
            # rebuilds wrappers from the run replacement before execution.
            return toolset
        return CodeActToolset(wrapped=toolset, config=self.config, state=self._state)

    def get_instructions(self):
        return self._live_instructions

    async def _live_instructions(self, ctx: RunContext[AgentContext]) -> str:
        if not ctx.realtime:
            return ""
        self._require_context(ctx)
        manager = ctx.tool_manager
        if manager is None or manager.tools is None:
            return ""
        # Realtime resolves instructions after final tool preparation, but never
        # invokes before_model_request. Advertise the same callable directory here.
        blocks: list[str] = []
        for tool in manager.tools.values():
            metadata = tool.tool_def.metadata or {}
            if metadata.get("a13n.codeact.runner") is not True:
                continue
            blocks.append(
                render_codeact_runner_description(
                    manager.tools,
                    program=metadata.get("a13n.codeact.program") is True,
                )
            )
        return "\n\n".join(blocks)

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
