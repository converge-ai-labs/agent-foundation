"""One model-facing surface and reminder for Host-owned background work."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import DynamicToolset

from a13n_harness.capabilities.subagents import SUBAGENT_CAPABILITY_ID, SubagentCapability
from a13n_harness.context import AgentContext
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    _RunNoticeState,
)
from a13n_harness.tool_execution import ToolExecutionRecord
from a13n_harness.toolsets.execution import ExecutionToolset, _operator_context

EXECUTION_CAPABILITY_ID = "a13n.executions"


@dataclass(init=False)
class ExecutionCapability(AbstractModelContextCapability):
    id = EXECUTION_CAPABILITY_ID

    def __init__(self) -> None:
        self._attention = _RunNoticeState[tuple[dict[str, str], bool]]()

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(EXECUTION_CAPABILITY_ID)
        if existing is None:
            existing = ExecutionCapability()
            ctx.deps._record_run_capability(EXECUTION_CAPABILITY_ID, existing)
        return existing

    def get_toolset(self) -> DynamicToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id="execution")

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]):
        subagents = ctx.capabilities.get(SUBAGENT_CAPABILITY_ID)
        return ExecutionToolset(
            owner=self, context=ctx.deps, subagents=subagents if isinstance(subagents, SubagentCapability) else None
        ).get_toolset()

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        projection = await handler(request)
        runtime = ctx.deps.tool_execution
        subagents = ctx.capabilities.get(SUBAGENT_CAPABILITY_ID)
        operator = subagents.operator if isinstance(subagents, SubagentCapability) else None
        if runtime is None and operator is None:
            return projection
        # Active executions and undelivered results form the leading priority
        # groups. One lookahead detects truncation without scanning old history.
        records = [
            record
            for record in (await runtime.summarize(ctx.deps.thread_id, limit=21) if runtime is not None else ())
            if not record.terminal or (record.result_available and not record.delivery_acknowledged)
        ]
        agents = await operator.attention(_operator_context(ctx.deps), limit=21) if operator is not None else ()
        previous = self._attention.previous
        before = previous[0] if previous else {}
        overflow = len(records) + len(agents) > 20
        entries = [(r.execution_id, r.tool_name, _attention_status(r)) for r in records]
        entries.extend((a.execution_id, a.name, a.state) for a in agents)
        entries.sort(key=lambda row: row[2].split(",", 1)[0] not in {"queued", "running"})
        current = {key: status for key, _, status in entries[:20]}
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
        for key, name, status in entries[:20]:
            record = next((r for r in records if r.execution_id == key), None)
            if status == before.get(key) or key in reported or (key, status) in receipts:
                continue
            if (
                record is not None
                and record.terminal
                and (
                    key in notices.completion_ids
                    or (
                        previous is not None
                        and record.foreground_released
                        and runtime is not None
                        and runtime.reports_background_completions
                    )
                )
            ):
                continue
            rows.append(f"- {key} | {name} | {status}")
        # Leaving a bounded page does not imply completion. Only announce a confirmed
        # terminal outcome for previously active work that has no retained result.
        for key in sorted(before.keys() - current.keys()):
            if key in reported or key in notices.completion_ids or "result available" in before[key]:
                continue
            if runtime is None:
                continue
            final = await runtime.info(ctx.deps.thread_id, key)
            if (
                final is not None
                and final.terminal
                and not final.result_available
                and not (final.foreground_released and runtime.reports_background_completions)
            ):
                rows.append(f"- {key} | {final.tool_name} | {_attention_status(final)}; inspect with execution_info")
        block = None
        if previous is None or rows or (overflow and not previous[1]):
            lines = [f"Background work ({min(len(rows), 20)}{'+' if overflow or len(rows) > 20 else ''})", *rows[:20]]
            if previous is None and not entries:
                lines.append(
                    "Use execution_info() to inspect prior outcomes; an empty list does not establish success."
                )
            if overflow or len(rows) > 20:
                lines.append("More executions may require attention; use execution_info to inspect.")
            block = ModelContextBlock(
                source_id="execution.summary",
                context_group="runtime_state",
                placement=ModelContextPlacement.REQUEST_EPILOGUE,
                content="\n".join(lines),
            )
        self._attention.observe(request, (current, overflow), block)
        return ModelContextProjection(blocks=(*projection.blocks, block)) if block else projection


def _attention_status(record: ToolExecutionRecord) -> str:
    status = record.status
    if record.terminal and record.result_available:
        return status + ", result available"
    if record.cancel_requested:
        return status + ", cancellation requested"
    return status
