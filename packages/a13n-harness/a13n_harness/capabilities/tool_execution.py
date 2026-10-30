"""Optional shared asynchronous execution and model-facing controls."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import DynamicToolset

from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
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
from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionRuntime
from a13n_harness.tool_execution import ToolExecutionConfig as ToolExecutionConfig

TOOL_EXECUTION_CAPABILITY_ID = "a13n.tool-execution"


@dataclass(init=False)
class ToolExecutionCapability(AbstractModelContextCapability):
    """Capture the wait policy and expose the Host-held Harness runtime."""

    id = TOOL_EXECUTION_CAPABILITY_ID

    def __init__(self, config: ToolExecutionConfig | None = None) -> None:
        self.config = config or ToolExecutionConfig()

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if self.config.mode == "auto" and ctx.deps.tool_execution is None:
            raise DefinitionError(
                "Automatic tool execution requires RunBindings.tool_execution.", code="tool_execution_unbound"
            )
        ctx.deps._tool_executions.config = self.config
        ctx.deps._tool_executions.enabled = ctx.deps.tool_execution is not None
        return self

    def get_toolset(self) -> DynamicToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id="tool-execution")

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> FunctionToolset[AgentContext] | None:
        if ctx.deps.tool_execution is None:
            return None
        return FunctionToolset(
            [tool_execution_info, tool_execution_result, tool_execution_wait, tool_execution_cancel],
            id="tool-execution",
            metadata={TOOL_EXECUTION_METADATA_KEY: "control"},
        )

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        projection = await handler(request)
        runtime = ctx.deps.tool_execution
        if runtime is None:
            return projection
        records = await runtime.summarize(ctx.deps.thread_id)
        if not records:
            return projection
        summary = json.dumps([record.model_dump(mode="json") for record in records], ensure_ascii=True)
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id="tool_execution.summary",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=f"Tool executions (status only; results are not consumed): {summary}",
                ),
            ),
        )


def _runtime(ctx: RunContext[AgentContext]) -> ToolExecutionRuntime:
    runtime = ctx.deps.tool_execution
    if runtime is None:
        raise ToolFailed("Tool execution is unavailable for this Run.")
    return runtime


async def tool_execution_info(
    ctx: RunContext[AgentContext],
    execution_id: str | None = None,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> Any:
    """Inspect an owned execution, or list a bounded page in this Thread."""
    runtime = _runtime(ctx)
    if execution_id is not None:
        record = await runtime.info(ctx.deps.thread_id, execution_id)
        return record.model_dump(mode="json") if record is not None else {"status": "unavailable"}
    return [
        record.model_dump(mode="json")
        for record in await runtime.summarize(ctx.deps.thread_id, offset=offset, limit=limit)
    ]


async def _result(ctx: RunContext[AgentContext], execution: StoredToolExecution | None) -> Any:
    if execution is None:
        return {"status": "unavailable"}
    if execution.retry_prompt is not None:
        # The original call has already handed off. Return its correction as data
        # rather than charging the retrieval tool's native retry budget.
        return {**execution.record.model_dump(mode="json"), "retry_prompt": execution.retry_prompt}
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
        return ctx.deps._tool_executions.delivered_result(ctx.tool_call_id, execution.record, result)
    return execution.record.model_dump(mode="json")


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


async def tool_execution_result(ctx: RunContext[AgentContext], execution_id: str) -> Any:
    """Retrieve a retained result, or report pending/unavailable. Receipts do not consume results."""
    return await _result(ctx, await _runtime(ctx).result(ctx.deps.thread_id, execution_id))


async def tool_execution_wait(
    ctx: RunContext[AgentContext],
    execution_id: str,
    timeout_seconds: Annotated[float, Field(ge=0, le=30, allow_inf_nan=False)] = 1.0,
) -> Any:
    """Wait boundedly without cancelling the execution when this wait is interrupted."""
    runtime = _runtime(ctx)
    execution = await ctx.deps._tool_executions.wait_control(
        lambda timeout: runtime.wait(ctx.deps.thread_id, execution_id, timeout_seconds=timeout),
        timeout_seconds=timeout_seconds,
    )
    return await _result(ctx, execution)


async def tool_execution_cancel(ctx: RunContext[AgentContext], execution_id: str) -> Any:
    """Request cancellation. Acceptance is not completion or proof of remote rollback."""
    record = await _runtime(ctx).cancel(ctx.deps.thread_id, execution_id)
    return record.model_dump(mode="json") if record is not None else {"status": "unavailable"}
