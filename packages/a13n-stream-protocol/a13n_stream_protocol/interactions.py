"""Host-owned pending interactions shared by reconnectable conversation surfaces."""

from typing import Literal, Self

from a13n_harness.mcp_inputs import McpTaskInputRequest
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class McpElicitation(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: Literal["mcp_elicitation"] = "mcp_elicitation"
    request_id: str
    thread_id: str
    run_id: str | None = None
    tool_call_id: str | None = None
    server_id: str
    server_name: str | None = None
    mode: Literal["form", "url"]
    message: str
    schema_: dict[str, JsonValue] | None = Field(default=None, alias="schema")
    url: str | None = None
    url_host: str | None = None
    expires_at: AwareDatetime

    @classmethod
    def project(
        cls,
        request: McpTaskInputRequest,
        *,
        request_id: str,
        thread_id: str,
        run_id: str | None,
        tool_call_id: str | None,
        server_id: str,
        server_name: str | None = None,
        expires_at: AwareDatetime,
    ) -> Self:
        return cls(
            request_id=request_id,
            thread_id=thread_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            server_id=server_id,
            server_name=server_name,
            mode=request.mode,
            message=request.message,
            schema=request.schema_,
            url=request.url,
            url_host=request.validate_prompt(),
            expires_at=expires_at,
        )


class PendingInteractions(BaseModel):
    """Replace the pending interactions for this thread, including with an empty list."""

    thread_id: str
    requests: tuple[McpElicitation, ...]
