"""Thin standard Toolset dispatch for Host-owned asynchronous subagents."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.capabilities.subagents import (
    AsyncDelegateRequest,
    AsyncExecutionView,
    AsyncResumeRequest,
    ResolvedDelegationContext,
    SubagentCancelRequest,
    SubagentCancelResult,
    SubagentDelegationPlan,
    SubagentInfoRequest,
    SubagentInfoResult,
    SubagentOperator,
    SubagentOperatorContext,
    SubagentSteerRequest,
    SubagentSteerResult,
    SubagentToolCallContext,
    SubagentWaitRequest,
    SubagentWaitResult,
)
from a13n_harness.context import AgentContext, BuiltSubagent
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.metadata import (
    MAX_OUTPUT_BYTES,
    HarnessTool,
    HarnessToolMetadata,
    IdempotencySemantics,
    ToolEffect,
    ToolOutputPolicy,
)
from a13n_harness.usage import intersect_usage_limits

from ._instructions import InstructionFunctionToolset, tool_instruction
from .delegation import _build_child_input

_STATUS_OUTPUT_POLICY = ToolOutputPolicy(
    max_inline_bytes=256 * 1024,
    max_output_bytes=4 * 1024 * 1024,
    overflow="truncate",
    redact=True,
)
_WAIT_OUTPUT_POLICY = ToolOutputPolicy(
    max_inline_bytes=256 * 1024,
    max_output_bytes=MAX_OUTPUT_BYTES,
    overflow="spill",
    redact=True,
)


class AsyncSubagentToolset:
    """Validate standard requests and delegate complete use cases to one Host operator."""

    def __init__(
        self,
        *,
        owner: AbstractCapability[AgentContext],
        context: AgentContext,
        operator: SubagentOperator,
    ) -> None:
        if not isinstance(operator, SubagentOperator):
            raise TypeError("operator must be a SubagentOperator")
        self._owner = owner
        self._context = context
        self._operator = operator

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tools = [
            self._tool(self.delegate, "subagents.async.delegate", {"execute"}, "none"),
            self._tool(self.subagent_info, "subagents.async.info", {"read"}, "read_only"),
            self._tool(
                self.wait_subagent,
                "subagents.async.wait",
                {"read", "execute"},
                "read_only",
                output_policy=_WAIT_OUTPUT_POLICY,
            ),
            self._tool(self.steer_subagent, "subagents.async.steer", {"write"}, "none"),
            self._tool(self.cancel_subagent, "subagents.async.cancel", {"write"}, "none"),
            self._tool(self.resume_subagent, "subagents.async.resume", {"execute"}, "none"),
        ]
        children = tuple(self._context.subagents.values())
        available = (
            "; ".join(f"{child.declaration.name}: {child.declaration.description[:512]}" for child in children[:64])
            if children
            else "none"
        )
        return InstructionFunctionToolset(
            tools=tools,
            id="a13n-async-subagent-tools",
            instructions=tool_instruction("subagents").format(available_subagents=available),
        )

    async def delegate(
        self,
        ctx: RunContext[AgentContext],
        subagent_name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$", max_length=63)],
        prompt: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
    ) -> JsonValue:
        self._require_context(ctx)
        child = self._require_child(subagent_name)
        request = AsyncDelegateRequest(subagent_name=subagent_name, prompt=prompt)
        plan = self._plan(ctx, child, prompt)
        result = _validate_model(
            AsyncExecutionView,
            await self._operator.delegate(plan, request, tool_call=_tool_call_context(ctx)),
        )
        _validate_execution(result, child, resumed_from=None)
        return result.model_dump(mode="json")

    async def subagent_info(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[str | None, Field(min_length=1, max_length=256)] = None,
        execution_offset: Annotated[int, Field(ge=0)] = 0,
        execution_limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> JsonValue:
        self._require_context(ctx)
        request = SubagentInfoRequest(
            execution_id=execution_id,
            execution_offset=execution_offset,
            execution_limit=execution_limit,
        )
        result = _validate_model(
            SubagentInfoResult,
            await self._operator.info(self._operator_context(), request, tool_call=_tool_call_context(ctx)),
        )
        if execution_id is not None:
            _require_exact_execution(result.executions, execution_id)
        return result.model_dump(mode="json")

    async def wait_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[str | None, Field(min_length=1, max_length=256)] = None,
        timeout_seconds: Annotated[float | None, Field(gt=0, allow_inf_nan=False)] = None,
        execution_offset: Annotated[int, Field(ge=0)] = 0,
        execution_limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> JsonValue:
        self._require_context(ctx)
        request = SubagentWaitRequest(
            execution_id=execution_id,
            timeout_seconds=timeout_seconds,
            execution_offset=execution_offset,
            execution_limit=execution_limit,
        )
        result = _validate_model(
            SubagentWaitResult,
            await self._operator.wait(self._operator_context(), request, tool_call=_tool_call_context(ctx)),
        )
        if execution_id is not None:
            _require_exact_execution(result.executions, execution_id)
        return result.model_dump(mode="json")

    async def steer_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[str, Field(min_length=1, max_length=256)],
        message: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
    ) -> JsonValue:
        self._require_context(ctx)
        request = SubagentSteerRequest(execution_id=execution_id, message=message)
        result = _validate_model(
            SubagentSteerResult,
            await self._operator.steer(self._operator_context(), request, tool_call=_tool_call_context(ctx)),
        )
        if result.execution_id != execution_id:
            raise TypeError("subagent operator retargeted a steering request")
        return result.model_dump(mode="json")

    async def cancel_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[str, Field(min_length=1, max_length=256)],
    ) -> JsonValue:
        self._require_context(ctx)
        request = SubagentCancelRequest(execution_id=execution_id)
        result = _validate_model(
            SubagentCancelResult,
            await self._operator.cancel(self._operator_context(), request, tool_call=_tool_call_context(ctx)),
        )
        if result.execution_id != execution_id:
            raise TypeError("subagent operator retargeted a cancellation request")
        return result.model_dump(mode="json")

    async def resume_subagent(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[str, Field(min_length=1, max_length=256)],
        prompt: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
    ) -> JsonValue:
        self._require_context(ctx)
        info_request = SubagentInfoRequest(execution_id=execution_id)
        info = _validate_model(
            SubagentInfoResult,
            await self._operator.info(self._operator_context(), info_request, tool_call=_tool_call_context(ctx)),
        )
        previous = _require_exact_execution(info.executions, execution_id)
        child = self._require_child(previous.subagent_name)
        if not previous.resumable:
            raise ToolFailed("The retained subagent execution is not resumable.")
        request = AsyncResumeRequest(execution_id=execution_id, prompt=prompt)
        result = _validate_model(
            AsyncExecutionView,
            await self._operator.resume(
                self._plan(ctx, child, prompt),
                request,
                tool_call=_tool_call_context(ctx),
            ),
        )
        _validate_execution(
            result,
            child,
            resumed_from=execution_id,
            allow_definition_replacement=True,
        )
        return result.model_dump(mode="json")

    def _plan(
        self,
        ctx: RunContext[AgentContext],
        child: BuiltSubagent,
        prompt: str,
    ) -> SubagentDelegationPlan:
        from a13n_harness.builder import derive_child_identity

        child_input = _build_child_input(ctx, child, prompt)
        limits = intersect_usage_limits(
            child.executable.definition_usage_limits(),
            child.declaration.usage_limits,
        )
        return SubagentDelegationPlan(
            child=child,
            child_identity=derive_child_identity(
                self._context.identity,
                child.definition.definition_id,
                child.declaration.identity,
            ),
            context=ResolvedDelegationContext(
                input=child_input,
                policy=child.declaration.context,
            ),
            usage_limits=limits,
            parent=self._operator_context(),
        )

    def _operator_context(self) -> SubagentOperatorContext:
        return SubagentOperatorContext(
            parent_thread_id=self._context.thread_id,
            parent_run_id=self._context.run_id,
            parent_agent_instance_id=self._context.instance.agent_instance_id,
            host_refs=self._context.instance.host_refs,
        )

    def _require_child(self, name: str) -> BuiltSubagent:
        try:
            return self._context.subagents.require(name)
        except KeyError as exc:
            raise ToolFailed("Unknown async subagent.") from exc

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        from a13n_harness.capabilities.subagents import SUBAGENT_CAPABILITY_ID

        if ctx.deps is not self._context or ctx.capabilities.get(SUBAGENT_CAPABILITY_ID) is not self._owner:
            raise DefinitionError(
                "Async subagent Toolset cannot cross logical runs.",
                code="capability_scope_invalid",
            )

    @staticmethod
    def _tool(
        function: Any,
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: IdempotencySemantics,
        *,
        output_policy: ToolOutputPolicy = _STATUS_OUTPUT_POLICY,
    ) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset(effects),
                credential_audiences=(),
                idempotency=idempotency,
                output_policy=output_policy,
            ),
        )


def _validate_model(model_type, value):
    if not isinstance(value, model_type):
        raise TypeError(f"subagent operator must return {model_type.__name__}")
    return model_type.model_validate(value.model_dump(mode="python"))


def _tool_call_context(ctx: RunContext[AgentContext]) -> SubagentToolCallContext:
    return SubagentToolCallContext(tool_call_id=ctx.tool_call_id, tool_name=ctx.tool_name)


def _require_exact_execution(executions, execution_id: str):
    if len(executions) != 1 or executions[0].execution_id != execution_id:
        raise TypeError("subagent operator returned the wrong execution")
    return executions[0]


def _validate_execution(
    result: AsyncExecutionView,
    child: BuiltSubagent,
    *,
    resumed_from: str | None,
    allow_definition_replacement: bool = False,
) -> None:
    if result.subagent_name != child.declaration.name:
        raise TypeError("subagent operator retargeted the selected child")
    if not allow_definition_replacement and result.child_definition_id != child.definition.definition_id:
        raise TypeError("subagent operator returned an incompatible child definition")
    if result.resumed_from != resumed_from:
        raise TypeError("subagent operator returned incompatible continuation linkage")


__all__ = ["AsyncSubagentToolset"]
