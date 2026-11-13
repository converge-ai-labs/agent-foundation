"""Optional shared asynchronous execution and model-facing controls."""

from __future__ import annotations

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
    _RunNoticeState,
)
from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionRecord, ToolExecutionRuntime
from a13n_harness.tool_execution import ToolExecutionConfig as ToolExecutionConfig

TOOL_EXECUTION_CAPABILITY_ID = "a13n.tool-execution"


@dataclass(init=False)
class ToolExecutionCapability(AbstractModelContextCapability):
    """Capture the wait policy and expose the Host-held Harness runtime."""

    id = TOOL_EXECUTION_CAPABILITY_ID

    def __init__(self, config: ToolExecutionConfig | None = None) -> None:
        self.config = config or ToolExecutionConfig()
        self._attention = _RunNoticeState[tuple[dict[str, str], bool]]()

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if self.config.mode == "auto" and ctx.deps.tool_execution is None:
            raise DefinitionError(
                "Automatic tool execution requires RunBindings.tool_execution.", code="tool_execution_unbound"
            )
        ctx.deps._tool_executions.config = self.config
        ctx.deps._tool_executions.enabled = ctx.deps.tool_execution is not None
        existing = ctx.deps._run_capability(TOOL_EXECUTION_CAPABILITY_ID)
        if existing is None:
            existing = ToolExecutionCapability(self.config)
            ctx.deps._record_run_capability(TOOL_EXECUTION_CAPABILITY_ID, existing)
        return existing

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
        # Active executions and undelivered results form the leading priority
        # groups. One lookahead detects truncation without scanning old history.
        records = [
            record
            for record in await runtime.summarize(ctx.deps.thread_id, limit=21)
            if not record.terminal or (record.result_available and not record.delivery_acknowledged)
        ]
        previous = self._attention.previous
        before = previous[0] if previous else {}
        overflow = len(records) > 20
        current = {record.execution_id: _attention_status(record) for record in records[:20]}
        if previous == (current, overflow):
            return projection

        notices = request._notices
        receipts = {
            (p.content.get("execution_id"), p.content.get("status"))
            for p in notices.results
            if isinstance(p.content, dict)
            and p.content.get("type") == "tool_execution_receipt"
            and isinstance(p.content.get("execution_id"), str)
            and isinstance(p.content.get("status"), str)
        }
        reported = {
            ctx.deps._tool_executions.result_origins[call_id].execution_id
            for call_id in notices.result_call_ids
            if call_id in ctx.deps._tool_executions.result_origins
        }
        rows = []
        for record in records[:20]:
            key = record.execution_id
            status = current[key]
            if status == before.get(key) or key in reported or (key, status) in receipts:
                continue
            if record.terminal and (
                key in notices.completion_ids
                or (previous is not None and record.foreground_released and runtime.reports_background_completions)
            ):
                continue
            rows.append(f"- {key} | {record.tool_name} | {status}")
        # Leaving a bounded page does not imply completion. Only announce a confirmed
        # terminal outcome for previously active work that has no retained result.
        for key in sorted(before.keys() - current.keys()):
            if key in reported or key in notices.completion_ids or "result available" in before[key]:
                continue
            final = await runtime.info(ctx.deps.thread_id, key)
            if (
                final is not None
                and final.terminal
                and not final.result_available
                and not (final.foreground_released and runtime.reports_background_completions)
            ):
                rows.append(
                    f"- {key} | {final.tool_name} | {_attention_status(final)}; inspect with tool_execution_info"
                )
        block = None
        if previous is None or rows or (overflow and not previous[1]):
            header = "Tool executions requiring attention" if previous is None else "Tool execution changes"
            lines = [f"{header} ({min(len(rows), 20)}{'+' if overflow or len(rows) > 20 else ''})", *rows[:20]]
            if previous is None and not records:
                lines.append(
                    "Use tool_execution_info() to inspect prior outcomes; an empty list does not establish success."
                )
            if overflow or len(rows) > 20:
                lines.append("More executions may require attention; use tool_execution_info to inspect.")
            block = ModelContextBlock(
                source_id="tool_execution.summary",
                context_group="runtime_state",
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="\n".join(lines),
            )
        self._attention.observe(request, (current, overflow), block)
        return ModelContextProjection(blocks=(*projection.blocks, block)) if block else projection


def _attention_status(record: ToolExecutionRecord) -> str:
    status = "finished" if record.status == "succeeded" else str(record.status)
    if record.terminal and record.result_available:
        return status + ", result available"
    if record.cancel_requested:
        return status + ", cancellation requested"
    return status


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
