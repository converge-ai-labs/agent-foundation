"""Agent Composer: the workspace's builtin agent that creates and changes agents with its users.

Its head has `source = 'builtin'` and `preset_kind = 'composer'`: its runs, threads and approvals are every
agent's, and it can be duplicated. The deployment owns its definition; the workspace owns the model it runs on, so
`prepare` builds it on demand. The first preparation creates the head; later preparations offer changed protected
configuration as revisions without replacing the workspace's selected default or model.
"""

from collections.abc import Sequence
from typing import Literal

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.tools.client import ClientToolDefinition
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.infra.db import Storage
from a13n_service.infra.platform import PLATFORM_GROUPS
from a13n_service.providers.registry import Registry
from a13n_service.resources.agents.schemas import Agent, AgentConfig
from a13n_service.resources.agents.toolsets import ToolSelection, ToolsetSelection, default_toolsets
from a13n_service.tenancy.authorize import Principal

NAME = "Agent Composer"
DESCRIPTION = "Create and refine your agents."


class ProviderConfiguration(BaseModel):
    """Non-secret proposal for the Console's native client tool."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "update"]
    kind: Literal["model", "environment", "connector", "web"]
    type: str = Field(min_length=1, description="Installed provider type from the platform catalog.")
    name: str = Field(min_length=1, max_length=128)
    config: dict[str, JsonValue] = Field(
        description="Complete non-secret provider configuration. Never include credentials."
    )
    provider_id: str | None = Field(
        default=None, description="Required for update; use the previously read provider ID."
    )
    etag: str | None = Field(
        default=None, description="Required for update; exact original ETag from the provider read."
    )


def provider_tool() -> ClientToolDefinition:
    return ClientToolDefinition(
        name="configure_provider",
        description="Ask the user to create or update a provider in a Console card. Credentials are entered privately.",
        parameters_json_schema=ProviderConfiguration.model_json_schema(),
        instruction=(
            "Read provider types and configuration schemas from the platform catalog first. For updates, read the "
            "provider first and supply its original ETag and complete desired configuration. Do not include API keys, "
            "tokens, client secrets or header values. The Console saves with the user's permission and returns only "
            "a status and provider ID. Cancelled means no save was attempted; unknown means a save could not be "
            "confirmed. Never automatically repeat an uncertain create."
        ),
    )


def configuration(model: str) -> AgentConfig:
    return AgentConfig(
        model=model,
        toolsets={
            "files": ToolsetSelection(
                tools={
                    key: ToolSelection(enabled=key in {"view", "ls", "glob", "grep"})
                    for key in default_toolsets()["files"].tools
                }
            ),
            "shell": ToolsetSelection(enabled=False),
            "memory": ToolsetSelection(enabled=False),
            **{group: ToolsetSelection() for group in PLATFORM_GROUPS},
        },
        builtin_skills=frozenset({"platform-operations"}),
        user_questions=True,
        client_tools=(provider_tool(),),
    )


async def prepare(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    preferred: Sequence[str],
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> Agent:
    from a13n_service.resources.agents import presets

    return await presets.prepare(
        storage,
        actor,
        workspace_id,
        kind="composer",
        name=NAME,
        description=DESCRIPTION,
        configuration=configuration,
        preferred=preferred,
        registry=registry,
        plugins=plugins,
    )
