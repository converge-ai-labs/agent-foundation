"""Unified controls for Host-owned agent and tool executions."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any

from pydantic import Field, JsonValue
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
from a13n_harness.capabilities.subagents import (
    AsyncDelegateRequest,
    AsyncExecutionView,
    AsyncResumeRequest,
    ResolvedDelegationContext,
    SubagentCancelRequest,
    SubagentCancelResult,
    SubagentCapability,
    SubagentDelegationPlan,
    SubagentExecutionView,
    SubagentInfoRequest,
    SubagentInfoResult,
    SubagentOperatorContext,
    SubagentSteerRequest,
    SubagentSteerResult,
    SubagentToolCallContext,
    SubagentWaitRequest,
    SubagentWaitResult,
)
from a13n_harness.context import AgentContext, BuiltSubagent
from a13n_harness.errors import DefinitionError
from a13n_harness.execution_identity import format_execution_header
from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionRuntime
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


class ExecutionToolset:
    """Dispatch one tool surface to the existing Host execution owners."""

    def __init__(
        self, *, owner: AbstractCapability[AgentContext], context: AgentContext, subagents: SubagentCapability | None
    ) -> None:
        self._owner = owner
        self._context = context
        self._subagents = subagents if subagents is not None and subagents.async_enabled else None

    @property
    def _operator(self):
        if self._subagents is None or self._subagents.operator is None:
            raise ToolFailed("Agent execution is unavailable for this Run.")
        return self._subagents.operator

    def get_toolset(self) -> FunctionToolset[AgentContext] | None:
        tools = []
        if self._subagents is not None or self._context.tool_execution is not None:
            controls: tuple[tuple[Any, str, set[ToolEffect]], ...] = (
                (self.execution_info, "info", {"read"}),
                (self.execution_result, "result", {"read"}),
                (self.execution_wait, "wait", {"read", "execute"}),
                (self.execution_cancel, "cancel", {"write"}),
            )
            for function, action, effects in controls:
                tool = self._tool(
                    function,
                    f"execution.{action}",
                    effects,
                    "none" if action == "cancel" else "read_only",
                    output_policy=_WAIT_OUTPUT_POLICY if action in {"wait", "result"} else _STATUS_OUTPUT_POLICY,
                )
                assert tool.metadata is not None
                tool.metadata[TOOL_EXECUTION_METADATA_KEY] = "control"
                tools.append(tool)
        if self._subagents is not None or self._context.message_router is not None:
            tool = self._tool(self.send_message, "execution.send_message", {"write", "execute"}, "none")
            assert tool.metadata is not None
            tool.metadata[TOOL_EXECUTION_METADATA_KEY] = "control"
            tools.append(tool)
        if self._subagents is not None and self._context.subagents:
            tools.append(self._tool(self.delegate, "subagents.async.delegate", {"execute"}, "none"))
        if not tools:
            return None
        children = tuple(self._context.subagents.values())
        available = (
            "; ".join(f"{c.declaration.name}: {c.declaration.description[:512]}" for c in children[:64]) or "none"
        )
        return InstructionFunctionToolset(
            tools=tools,
            id="a13n-execution-tools",
            instructions=tool_instruction("subagents").format(available_subagents=available)
            if self._subagents is not None
            else "",
        )

    async def _agent_info(
        self, ctx: RunContext[AgentContext], execution_id: str | None = None, offset: int = 0, limit: int = 20
    ) -> SubagentInfoResult:
        result = _validate_model(
            SubagentInfoResult,
            await self._operator.info(
                self._operator_context(),
                SubagentInfoRequest(execution_id=execution_id, execution_offset=offset, execution_limit=limit),
                tool_call=_tool_call_context(ctx),
            ),
        )
        if execution_id is not None:
            _require_exact_execution(result.executions, execution_id)
        return result

    async def execution_info(
        self,
        ctx: RunContext[AgentContext],
        execution_id: Annotated[str | None, Field(min_length=1, max_length=256)] = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> Any:
        """Inspect one owned execution, or one bounded page per kind in this Thread."""
        self._require_context(ctx)
        if execution_id is not None:
            if execution_id.startswith("exec-"):
                record = await _runtime(ctx).info(ctx.deps.thread_id, execution_id)
                return record.model_dump(mode="json") if record is not None else {"status": "unavailable"}
            return _execution_view(
                _require_exact_execution((await self._agent_info(ctx, execution_id)).executions, execution_id)
            )
        agents = (
            _execution_page(await self._agent_info(ctx, offset=offset, limit=limit))
            if self._subagents is not None
            else {"executions": [], "execution_offset": offset, "total": 0, "next_offset": None}
        )
        runtime = ctx.deps.tool_execution
        tools = (
            [r.model_dump(mode="json") for r in await runtime.summarize(ctx.deps.thread_id, offset=offset, limit=limit)]
            if runtime is not None
            else []
        )
        return {"agent_executions": agents, "tool_executions": tools}

    async def execution_result(self, ctx: RunContext[AgentContext], execution_id: str) -> Any:
        """Retrieve a retained tool result or the Host's agent view and final reply when available."""
        self._require_context(ctx)
        if execution_id.startswith("exec-"):
            return await _result(ctx, await _runtime(ctx).result(ctx.deps.thread_id, execution_id))
        view = _require_exact_execution((await self._agent_info(ctx, execution_id)).executions, execution_id)
        value = _execution_view(view)
        return (
            value
            if view.output is not None
            else ToolReturn(return_value=value, content=["Final output is unavailable for this agent execution."])
        )

    async def execution_wait(
        self,
        ctx: RunContext[AgentContext],
        execution_id: str,
        timeout_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 30,
    ) -> Any:
        """Wait for one execution, without cancelling it when the wait ends or is interrupted."""
        self._require_context(ctx)
        if execution_id.startswith("exec-"):
            if timeout_seconds > 30:
                raise ToolFailed("Tool execution waits have a maximum of 30 seconds.")
            runtime = _runtime(ctx)
            execution = await ctx.deps._tool_executions.wait_control(
                lambda timeout: runtime.wait(ctx.deps.thread_id, execution_id, timeout_seconds=timeout),
                timeout_seconds=timeout_seconds,
            )
            return await _result(ctx, execution)
        if timeout_seconds == 0:
            return await self.execution_info(ctx, execution_id)
        result = _validate_model(
            SubagentWaitResult,
            await self._operator.wait(
                self._operator_context(),
                SubagentWaitRequest(execution_id=execution_id, timeout_seconds=timeout_seconds),
                tool_call=_tool_call_context(ctx),
            ),
        )
        return _execution_view(_require_exact_execution(result.executions, execution_id))

    async def execution_cancel(self, ctx: RunContext[AgentContext], execution_id: str) -> Any:
        """Request cancellation; acceptance is not completion or proof of remote rollback."""
        self._require_context(ctx)
        if execution_id.startswith("exec-"):
            record = await _runtime(ctx).cancel(ctx.deps.thread_id, execution_id)
            return record.model_dump(mode="json") if record is not None else {"status": "unavailable"}
        result = _validate_model(
            SubagentCancelResult,
            await self._operator.cancel(
                self._operator_context(),
                SubagentCancelRequest(execution_id=execution_id),
                tool_call=_tool_call_context(ctx),
            ),
        )
        if result.execution_id != execution_id:
            raise TypeError("subagent operator retargeted a cancellation request")
        return result.model_dump(mode="json")

    async def send_message(
        self,
        ctx: RunContext[AgentContext],
        to: Annotated[str, Field(min_length=1, max_length=256)],
        message: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
    ) -> Any:
        """Send input to an agent execution or Host target. Acceptance does not mean it was read."""
        self._require_context(ctx)
        # Routing is by address namespace, never fallback after an ownership or authorization failure.
        if to in {"parent", "bot", "live"} or to.startswith("thread_"):
            router = ctx.deps.message_router
            if router is None:
                raise ToolFailed(f"This Host has no MessageRouter for target {to!r}.")
            return await router.send(ctx, to, message)
        if to.startswith("exec-"):
            raise ToolFailed("Messages cannot be sent to tool executions.")
        previous = _require_exact_execution((await self._agent_info(ctx, to)).executions, to)
        if previous.status == "running":
            result = _validate_model(
                SubagentSteerResult,
                await self._operator.steer(
                    self._operator_context(),
                    SubagentSteerRequest(execution_id=to, message=message),
                    tool_call=_tool_call_context(ctx),
                ),
            )
            if result.execution_id != to:
                raise TypeError("subagent operator retargeted a steering request")
            if not result.accepted:
                raise ToolFailed("The running agent execution did not accept the message.")
            return result.model_dump(mode="json")
        if previous.status not in {"queued", "running"} and previous.resumable:
            return {"accepted": True, **await self._resume(ctx, previous, message)}
        raise ToolFailed(f"Agent execution in state {previous.status!r} cannot accept a message.")

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

    async def _resume(
        self, ctx: RunContext[AgentContext], previous: SubagentExecutionView, prompt: str
    ) -> dict[str, Any]:
        execution_id = previous.execution_id
        request = AsyncResumeRequest(execution_id=execution_id, prompt=prompt)
        if previous.source == "host":
            result = _validate_model(
                AsyncExecutionView,
                await self._operator.resume_host(self._operator_context(), request, tool_call=_tool_call_context(ctx)),
            )
            if (
                result.source != "host"
                or result.child_definition_id != previous.child_definition_id
                or result.subagent_name != previous.subagent_name
                or result.resumed_from != execution_id
                or result.execution_id == execution_id
            ):
                raise TypeError("subagent operator returned incompatible Host continuation")
            return result.model_dump(mode="json")
        child = self._require_child(previous.subagent_name)
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

        from .delegation import _build_child_input

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
        return _operator_context(self._context)

    def _require_child(self, name: str) -> BuiltSubagent:
        try:
            return self._context.subagents.require(name)
        except KeyError as exc:
            raise ToolFailed("Unknown async subagent.") from exc

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps is not self._context or ctx.capabilities.get("a13n.executions") is not self._owner:
            raise DefinitionError(
                "Execution Toolset cannot cross logical runs.",
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


def _execution_view(view: SubagentExecutionView) -> dict[str, Any]:
    return {
        "header": format_execution_header(
            "Subagent notification" if view.status in {"queued", "running"} else "Subagent result",
            view.subagent_name,
            view.execution_id,
            view.status,
        ),
        **view.model_dump(mode="json"),
        **({"reply_tool": "send_message"} if view.status == "running" or view.resumable else {}),
    }


def _execution_page(result: SubagentInfoResult | SubagentWaitResult) -> dict[str, Any]:
    return {**result.model_dump(mode="json"), "executions": [_execution_view(v) for v in result.executions]}


def _operator_context(context: AgentContext) -> SubagentOperatorContext:
    return SubagentOperatorContext(
        parent_thread_id=context.thread_id,
        parent_run_id=context.run_id,
        parent_agent_instance_id=context.instance.agent_instance_id,
        host_refs=context.instance.host_refs,
    )


def _runtime(ctx: RunContext[AgentContext]) -> ToolExecutionRuntime:
    if ctx.deps.tool_execution is None:
        raise ToolFailed("Tool execution is unavailable for this Run.")
    return ctx.deps.tool_execution


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
    if result.source != "declared":
        raise TypeError("declared subagent operator returned a Host-defined execution")
    if result.subagent_name != child.declaration.name:
        raise TypeError("subagent operator retargeted the selected child")
    if not allow_definition_replacement and result.child_definition_id != child.definition.definition_id:
        raise TypeError("subagent operator returned an incompatible child definition")
    if result.resumed_from != resumed_from:
        raise TypeError("subagent operator returned incompatible continuation linkage")


async def _result(ctx: RunContext[AgentContext], execution: StoredToolExecution | None) -> Any:
    if execution is None:
        return {"status": "unavailable"}
    kind = (
        "Tool result" if execution.record.result_available and execution.retry_prompt is None else "Tool notification"
    )
    header = format_execution_header(
        kind, execution.record.tool_name, execution.record.execution_id, execution.record.status
    )
    if execution.retry_prompt is not None:
        # The original call has already handed off. Return its correction as data
        # rather than charging the retrieval tool's native retry budget.
        return {"header": header, **execution.record.model_dump(mode="json"), "retry_prompt": execution.retry_prompt}
    if execution.record.result_available:
        assert ctx.tool_call_id is not None
        paths: dict[str, str] = {}
        for artifact in execution.artifacts:
            path = await ctx.deps._spill_tool_result(artifact.data, suffix=artifact.suffix)
            if path is None:
                raise ToolFailed(
                    "The result is retained, but its artifacts cannot be materialized in this Environment."
                )
            paths[artifact.path] = path
        result = _replace_artifact_paths(execution.result, paths) if paths else execution.result
        if isinstance(result, ToolReturn):
            content = [result.content] if isinstance(result.content, str) else list(result.content or ())
            result = replace(result, content=[header, *content])
        else:
            result = ToolReturn(return_value=result, content=[header])
        return ctx.deps._tool_executions.delivered_result(ctx.tool_call_id, execution.record, result)
    return {"header": header, **execution.record.model_dump(mode="json")}


def _replace_artifact_paths(value: Any, paths: dict[str, str]) -> Any:
    if isinstance(value, ToolReturn):
        return replace(
            value,
            return_value=_replace_artifact_paths(value.return_value, paths),
            content=_replace_artifact_paths(value.content, paths),
        )
    if isinstance(value, str):
        for original, current in paths.items():
            value = value.replace(original, current)
        return value
    if isinstance(value, dict):
        return {key: _replace_artifact_paths(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_artifact_paths(item, paths) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_artifact_paths(item, paths) for item in value)
    return value


__all__ = ["ExecutionToolset"]
