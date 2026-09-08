"""Typed invocation intent shared by application commands and protocol decoding."""

from pydantic import Field

from a13n_service.agents.domain import AgentRunOverride
from a13n_service.environments.domain import EnvironmentSelection
from a13n_service.environments.selection import Omitted
from a13n_service.hooks.domain import InlineHookRequest, InlineHookSubscriptionInput

from .domain import StrictModel
from .input import AgentInput


class StartRunIntent(StrictModel):
    agent_id: str
    input: AgentInput
    session_id: str | None = None
    agent_revision_id: str | None = None
    expected_current_revision_id: str | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None


class StartRunCommand(StartRunIntent):
    environment: EnvironmentSelection | Omitted | None = Omitted.UNSET


class ContinueRunIntent(StrictModel):
    expected_thread_version: int = Field(ge=1)
    input: AgentInput
    agent_id: str | None = None
    agent_revision_id: str | None = None
    expected_current_revision_id: str | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None


class ContinueRunCommand(ContinueRunIntent):
    environment: EnvironmentSelection | Omitted | None = Omitted.UNSET


class WaitingContinueRunCommand(InlineHookRequest):
    expected_thread_version: int = Field(ge=1)
    sealed_state_digest_sha256: str
    input: AgentInput


class RetryRunCommand(InlineHookRequest):
    expected_thread_version: int = Field(ge=1)


class ForkRunIntent(StrictModel):
    input: AgentInput
    agent_id: str | None = None
    agent_revision_id: str | None = None
    expected_current_revision_id: str | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None


class ForkRunCommand(ForkRunIntent):
    environment: EnvironmentSelection | Omitted | None = Omitted.UNSET
