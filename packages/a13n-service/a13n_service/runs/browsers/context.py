"""Browser availability projection and registration-only handback tool."""

from html import escape

from a13n_harness import AgentContext
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import Lease
from a13n_service.runs.browsers import admission
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tools import tool_failures

MESSAGES = {
    "available": "Browser automation is available. After handback, observe afresh before acting.",
    "takeover": "Human takeover blocks browser page operations. Call browser_notify_on_handback once if you need "
    "a reminder, then continue independent work or end the turn. Do not poll.",
    "not_ready": "Browser runtime is not ready. Ordinary browser tools retain their lazy preparation behavior; "
    "this context does not prepare it or register a handback reminder.",
}


class BrowserContextCapability(AbstractModelContextCapability):
    id = "a13n.service.browser-context"

    def __init__(self, runtime: Runtime, lease: Lease, binding: BrowserBinding) -> None:
        self.runtime, self.lease, self.binding = runtime, lease, binding

    async def notify_on_handback(self) -> dict[str, str]:
        with tool_failures():
            try:
                state = await admission.availability(self.runtime, self.lease, self.binding, notify=True)
            except BrowserError as exc:
                return {"status": "unavailable", "message": str(exc)}
        if state == "takeover":
            return {
                "status": "registered",
                "message": "One handback reminder is registered for this Thread. "
                "Continue independent work or end the turn; do not poll. Cancellation before viewer grant suppresses it.",
            }
        return {
            "status": "already_available" if state == "available" else "not_ready",
            "message": MESSAGES[state] + " No reminder was registered.",
        }

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.notify_on_handback,
                    name="browser_notify_on_handback",
                    description="Register one handback reminder without page access, preparation or waiting. Safe during human takeover; repeated requests coalesce.",
                    harness_metadata=HarnessToolMetadata(
                        tool_id="browser.notify_on_handback",
                        effects=frozenset({"write"}),
                        credential_audiences=(),
                        idempotency="none",
                        output_policy=ToolOutputPolicy(max_inline_bytes=4096, max_output_bytes=4096),
                    ),
                )
            ],
            id=self.id,
        )

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        try:
            message = MESSAGES[await admission.availability(self.runtime, self.lease, self.binding)]
        except (BrowserError, ServiceError) as exc:
            message = f"Browser unavailable: {exc} No handback reminder was registered."
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id="a13n.service.browser-context",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=f"<browser-context>\n{escape(message, quote=False)}\n</browser-context>",
                ),
            )
        )
