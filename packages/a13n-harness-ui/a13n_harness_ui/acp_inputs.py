"""Project ACP interactions onto the existing in-flight input surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from a13n_harness.toolsets.interaction import AskUserQuestionRequest
from acp import schema
from fastmcp.client.elicitation import ElicitResult
from mcp.types import ElicitRequestFormParams, ElicitRequestParams, ElicitRequestURLParams

from a13n_harness_ui.mcp_runtime.inputs import Inputs


@dataclass(frozen=True, slots=True)
class AcpInputs:
    inputs: Inputs
    thread_id: str
    agent_id: str
    require_active: Callable[[], None]
    run_id: str | None = None
    timeout_seconds: float | None = None

    async def request(
        self, params: ElicitRequestParams | AskUserQuestionRequest, tool_call_id: str | None
    ) -> ElicitResult[Any]:
        return await self.inputs.request(
            params,
            thread_id=self.thread_id,
            server_id=self.agent_id,
            run_id=self.run_id,
            tool_call_id=tool_call_id,
            require_active=self.require_active,
            timeout_seconds=self.timeout_seconds,
        )

    async def permission(self, call: schema.ToolCallUpdate, options: tuple[schema.PermissionOption, ...]) -> str | None:
        if not options:
            return None
        result = await self.request(
            ElicitRequestFormParams(
                message=(call.title or "External Agent is requesting permission")[:8192],
                requested_schema={
                    "type": "object",
                    "properties": {
                        "option": {
                            "type": "string",
                            "title": "Permission",
                            "oneOf": [{"const": option.option_id, "title": option.name} for option in options],
                        },
                    },
                    "required": ["option"],
                },
            ),
            call.tool_call_id,
        )
        return result.content["option"] if result.action == "accept" and result.content is not None else None

    async def elicitation(self, message: str, mode: str, params: dict[str, Any]) -> schema.CreateElicitationResponse:
        if mode == "form":
            requested = params["requested_schema"]
            request: ElicitRequestParams = ElicitRequestFormParams(
                message=message[:8192],
                requested_schema=(
                    requested.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if isinstance(requested, schema.ElicitationSchema)
                    else requested
                ),
            )
        elif mode == "url":
            request = ElicitRequestURLParams.model_validate(
                {"message": message[:8192], "url": params["url"], "elicitationId": params["elicitation_id"]}
            )
        else:
            return schema.CancelElicitationResponse()
        result = await self.request(request, params.get("tool_call_id"))
        if result.action == "accept":
            return schema.AcceptElicitationResponse(content=result.content)
        if result.action == "decline":
            return schema.DeclineElicitationResponse()
        return schema.CancelElicitationResponse()
