"""Current-Run browser availability and explicit process-local handback registration."""

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

from a13n_harness_ui.browser import HostBrowserBackend
from a13n_harness_ui.errors import HarnessUiError

AVAILABLE = "Browser automation is available. After handback, observe afresh before acting."
TAKEOVER = (
    "Human takeover blocks browser page operations. Call browser_notify_on_handback once if you need "
    "a reminder, then continue independent work or end the turn. Do not poll."
)


class BrowserContextCapability(AbstractModelContextCapability):
    id = "a13n-harness-ui.browser-context"

    def __init__(self, backend: HostBrowserBackend | None = None, *, unavailable: str = "") -> None:
        self.backend = backend
        self.unavailable = unavailable

    async def availability(self) -> bool:
        if self.backend is None:
            raise BrowserError(self.unavailable)
        browsers = self.backend.browsers
        await browsers.authorize(self.backend.thread_id)
        item = browsers.checked(self.backend.thread_id, self.backend.binding)
        if not browsers.runtime_ready or item.status != "ready" or item.scope is None or item.closed.is_set():
            raise BrowserError(
                "Browser binding is unavailable. Ask the user to reopen the browser; handback cannot repair it."
            )
        return not browsers.human_thread

    async def notify_on_handback(self) -> dict[str, str]:
        try:
            # No await between checking the gate and registering. Takeover/release use the same event loop.
            if await self.availability():
                return {"status": "already_available", "message": AVAILABLE + " No reminder was registered."}
            assert self.backend is not None
            return self.backend.browsers.handback.register(self.backend.thread_id)
        except (BrowserError, HarnessUiError) as exc:
            return {"status": "unavailable", "message": str(exc)}

    def get_toolset(self) -> FunctionToolset[AgentContext] | None:
        if self.backend is None:
            return None
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.notify_on_handback,
                    name="browser_notify_on_handback",
                    description="Register one handback reminder without accessing the page or waiting. Safe during human takeover; repeated requests coalesce.",
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
            message = AVAILABLE if await self.availability() else TAKEOVER
        except (BrowserError, HarnessUiError) as exc:
            message = f"Browser unavailable: {exc}"
            if self.backend is None:
                message += (
                    " Browser tools are absent for this Run. Continue other work; a later Run can capture recovery."
                )
        block = ModelContextBlock(
            source_id="a13n-harness-ui.browser-context",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content=f"<browser-context>\n{escape(message, quote=False)}\n</browser-context>",
        )
        if request.history.matches_latest(block):
            return projection
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                block,
            )
        )
