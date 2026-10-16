"""The root Agent can request assistance; it never receives a viewer credential."""

from typing import Annotated

from a13n_harness import AgentContext
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import CallDeferred, ToolFailed
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_service.runs.attempts import Lease
from a13n_service.runs.desktop_assistance import admission, service
from a13n_service.runs.desktop_assistance.schemas import Request
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tools import tool_failures
from a13n_service.tenancy.authorize import Principal


class DesktopAssistanceCapability(AbstractCapability[AgentContext]):
    id = "a13n.service.desktop-assistance"

    def __init__(self, runtime: Runtime, lease: Lease, principal: Principal):
        self.runtime, self.lease, self.principal = runtime, lease, principal

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.request_desktop_assistance,
                    takes_ctx=True,
                    description="Ask a person to help on an assistance-enabled desktop mount and wait. Explain the task without requesting secrets in chat. Do not send further desktop actions after requesting assistance. After continuation, observe the current screen and replan before any input.",
                    harness_metadata=HarnessToolMetadata(
                        tool_id="service.desktop_assistance.request",
                        effects=frozenset({"write"}),
                        idempotency="none",
                        credential_audiences=(),
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate", redact=True
                        ),
                    ),
                )
            ],
            id="a13n-service-desktop-assistance",
        )

    async def request_desktop_assistance(
        self,
        ctx: RunContext[AgentContext],
        mount: Annotated[str, Field(min_length=1, max_length=63)],
        reason: Annotated[str, Field(min_length=1, max_length=2048)],
    ) -> str:
        if not ctx.deps.deferred_tools_supported or ctx.tool_call_id is None:
            raise ToolFailed("Desktop assistance requires a resumable root run")
        with tool_failures():
            identity = await service.request_assistance(
                self.runtime, self.lease, self.principal, ctx.tool_call_id, Request(mount=mount, reason=reason)
            )
        if identity is None:
            return "The user already requested desktop control. This assistance request was not opened; wait for handback, then observe and replan."
        raise CallDeferred(metadata={"desktop_assistance_id": identity})

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        await admission.replan(self.runtime, self.lease)
        return request_context
