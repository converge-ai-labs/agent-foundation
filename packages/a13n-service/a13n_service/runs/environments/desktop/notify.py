"""Explicit Computer handback registration using the same recipients as blocked calls."""

from typing import Annotated

from a13n_harness import AgentContext
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_service.resources.agents.toolsets import COMPUTER_NOTIFY_TOOL_ID
from a13n_service.runs.attempts import Lease
from a13n_service.runs.environments.desktop.admission import register_handback
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tools import tool_failures


class HandbackNotifications(AbstractCapability[AgentContext]):
    id = "a13n.service.computer-notify"

    def __init__(self, runtime: Runtime, lease: Lease):
        self.runtime, self.lease = runtime, lease

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.computer_notify,
                    name="computer_notify",
                    description=(
                        "Register this Thread for one notice when the current Computer takeover ends. "
                        "Use when context reports human control, without making a blocked Computer call. "
                        "Registration is deduplicated with blocked calls. Continue other work or finish; do not poll. "
                        "If control has already returned, reports already_available without subscribing to future takeovers."
                    ),
                    harness_metadata=HarnessToolMetadata(
                        tool_id=COMPUTER_NOTIFY_TOOL_ID,
                        effects=frozenset({"write"}),
                        credential_audiences=(),
                        idempotency="none",
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                        ),
                    ),
                )
            ],
            id="a13n-service-computer-notify",
        )

    async def computer_notify(
        self,
        ctx: RunContext[AgentContext],
        alias: Annotated[str, Field(min_length=1, max_length=256, description="Current Environment mount name")],
    ) -> dict[str, JsonValue]:
        with tool_failures():
            registered = await register_handback(
                self.runtime, self.lease, alias, ctx.deps.environment.dump_states().get(alias)
            )
        return {
            "status": "registered" if registered else "already_available",
            "alias": alias,
            "hint": (
                "One-time notice registered for this takeover. Continue other work or finish; do not poll."
                if registered
                else "Human control has ended. Observe afresh before deciding on Computer input."
            ),
        }
