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
    ModelContextRequestKind,
)
from pydantic_ai import RunContext


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
        if request.kind is ModelContextRequestKind.TOOL_RESULTS:
            content += (
                "\nThis is automatic runtime metadata, not a new user message. "
                "Continue the existing task only if work remains; otherwise finish your turn. "
                "Do not acknowledge this metadata."
            )
        return ModelContextProjection(
            blocks=(
                *(block for block in projection.blocks if block.source_id != "a13n.agent-context"),
                ModelContextBlock(
                    source_id="a13n.agent-context",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=content,
                ),
            )
        )
