"""Model-visible identity for every Harness UI Agent invocation."""

from __future__ import annotations

from collections.abc import Callable
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
    """Replace Harness's default identity with the current Thread and model name."""

    id = "a13n-harness-ui.agent-context"

    def __init__(self, model_name_provider: Callable[[], str | None] | None = None) -> None:
        self._model_name_provider = model_name_provider

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        model_name = ctx.model.model_name if ctx.model is not None else None
        if ctx.model is None and self._model_name_provider is not None:
            model_name = self._model_name_provider()
        model_identity = (
            f"Current Model: {escape(name, quote=False)}\n"
            "Use the current Model name when attributing work to a model.\n"
            if model_name is not None and (name := model_name.strip())
            else ""
        )
        content = (
            '<agent-context source="a13n-harness-ui">\n'
            f"Current Thread: {escape(ctx.deps.thread_id, quote=False)}\n"
            f"{model_identity}"
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
                *(block for block in owned if not request.history.matches_latest(block)),
            )
        )
