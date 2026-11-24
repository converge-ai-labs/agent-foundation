"""Generate composer suggestions after a root continuation settles."""

from collections.abc import Awaitable, Callable

from a13n_harness import AbstractHarnessPlugin, PluginPostRunContext
from pydantic import Field

from a13n_harness_ui.surfaces import SurfaceModel

PREDICTION_PROMPT = (
    "Predict the single most likely next message the user would type in this chat. Match their language, tone, "
    "and level of detail. Return only the raw user message, without surrounding quotes, "
    "Markdown, preamble, or assistant phrasing. Return no text if the conversation is complete or there is no "
    "clear, concrete next step. Never call tools."
)


class InputPredictionRequest(SurfaceModel):
    continuation_id: str = Field(min_length=1, max_length=128)


class InputPredictionResponse(SurfaceModel):
    continuation_id: str
    text: str | None


class InputPredictionPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self) -> str:
        return "a13n.harness_ui.input_prediction"

    def __init__(
        self,
        eligible: Callable[[str, str, str], Awaitable[bool]],
        publish: Callable[[str, str, str, str], Awaitable[None]],
    ) -> None:
        self._eligible = eligible
        self._publish = publish

    async def post_run(self, context: PluginPostRunContext) -> None:
        if context.result.status != "completed" or context.result.deferred is not None:
            return
        continuation_id = context.metadata.get("continuation_id")
        execution_id = context.metadata.get("execution_id")
        if not isinstance(continuation_id, str) or not isinstance(execution_id, str):
            return
        if not await self._eligible(context.thread_id, continuation_id, execution_id):
            return
        raw = await context.agent.complete_text(PREDICTION_PROMPT, source="input_prediction")
        if raw is not None and (text := " ".join(raw.split())):
            await self._publish(context.thread_id, continuation_id, execution_id, text)
