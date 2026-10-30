"""The admission seam: built-in run limits plus an optional distribution policy.

`accept` runs inside `start_run`, so every run-creation path passes it. `proceed` runs before every paid
dispatch. Both are SQL-only, short and idempotent; a policy can only refuse what core checks allowed.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class AcceptedIntent:
    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    principal_id: str
    agent_id: str
    agent_revision_id: str
    trigger: str
    root_run_id: str


@dataclass(frozen=True, slots=True)
class CallContext:
    """One paid dispatch, identified before it is sent so usage reports correlate by `call_id`."""

    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    run_attempt_id: str
    root_run_id: str
    call_id: str
    source: str
    # None for a call served by no provider resource, such as a tool of a remote MCP server.
    provider_id: str | None
    model_id: str | None = None
    connection_id: str | None = None
    tool_name: str | None = None
    # None means the price is unknown; a budget policy decides how to treat that.
    price_snapshot: dict[str, JsonValue] | None = None


class AdmissionPolicy(Protocol):
    async def accept(self, session: AsyncSession, intent: AcceptedIntent) -> None: ...

    async def proceed(self, session: AsyncSession, call: CallContext) -> None: ...


@dataclass(frozen=True, slots=True)
class AppCallContext:
    """A direct user action attributed to a saved App, without inventing a worker attempt."""

    workspace_id: str
    principal_id: str
    run_id: str
    app_id: str
    connection_id: str
    tool_name: str
    call_id: str


class AppAdmissionPolicy(Protocol):
    """Optional extension; deployments with admission must opt in before direct App tool use."""

    async def proceed_app(self, session: AsyncSession, call: AppCallContext) -> None: ...
