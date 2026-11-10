"""Model-visible identity for every Harness UI Agent invocation."""

from __future__ import annotations

from html import escape

from a13n_harness import AgentContext
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from pydantic_ai import RunContext

from a13n_harness_ui.surface_context import surface_context


class CurrentAgentContextCapability(AbstractModelContextCapability):
    """Replace Harness's default identity with the current Thread and native Model name."""

    id = "a13n-harness-ui.agent-context"

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        content = (
            '<agent-context source="a13n-harness-ui">\n'
            f"Current Thread: {escape(ctx.deps.thread_id, quote=False)}\n"
            f"Current Model: {escape(ctx.model.model_name, quote=False)}\n"
            "Use the current Model name when attributing work to a model.\n"
            "</agent-context>"
        )
        content += (
            "\nThis is automatic runtime metadata, not a new user message. "
            "Continue the existing task only if work remains; otherwise finish your turn. "
            "Do not acknowledge this metadata."
        )
        blocks = tuple(block for block in projection.blocks if block.source_id != "a13n.agent-context")
        current = ModelContextBlock(
            source_id="a13n.agent-context",
            context_group="runtime_state",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content=content,
        )
        owned = (*surface_context(ctx.messages), current)
        return ModelContextProjection(
            blocks=(
                *blocks,
                *(block for block in owned if request.history.latest(block.source_id, block.key) != block.content),
            )
        )
