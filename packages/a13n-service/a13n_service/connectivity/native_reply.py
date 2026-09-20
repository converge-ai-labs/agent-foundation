"""Require an explicit native action before treating text as a delivered reply."""

from collections.abc import Awaitable, Callable
from typing import Any

from a13n_harness import AgentContext
from pydantic import JsonValue
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.output import OutputContext


class NativeReplyCapability(MCP[AgentContext]):
    def __init__(
        self, capability: MCP[AgentContext], *, recorded_reply: Callable[[], Awaitable[bool]] | None = None
    ) -> None:
        super().__init__(local=capability.get_toolset(), id=capability.id)  # type: ignore[arg-type]
        self.reply_attempted = False
        self.recorded_reply = recorded_reply

    def observe(self, result: JsonValue) -> None:
        # Never encourage a second send after an uncertain external side effect.
        if isinstance(result, dict) and result.get("kind") in {"succeeded", "outcome_unknown"}:
            self.reply_attempted = True

    async def before_output_process(
        self, ctx: RunContext[AgentContext], *, output_context: OutputContext, output: Any
    ) -> Any:
        if not ctx.partial_output and isinstance(output, str) and output.strip() and not self.reply_attempted:
            if self.recorded_reply is not None and await self.recorded_reply():
                return output
            raise ModelRetry(
                "Your final text has not been sent to the conversation. Use the bound reply tool to send the answer "
                "and check its outcome before finishing. If no reply is appropriate, return an empty final text. "
                "Do not repeat an action whose outcome is unknown."
            )
        return output
