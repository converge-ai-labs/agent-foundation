"""Notion's official Remote MCP; permissions are selected in upstream consent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpOAuthConfiguration, McpRemoteTransport
from a13n_harness_ui.connector_models import ConnectorConfiguration

from .models import Declaration
from .oauth_mcp import OAuthSetup
from .remote_mcp import RemoteMcp
from .sdk import Context, Controller, Definition, SetupFlow


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Notion(Definition):
    configuration_model = Settings

    def declaration(self) -> Declaration:
        return Declaration(
            key="notion",
            name="Notion",
            brand="notion",
            description="Search and work with the pages allowed by your Notion authorization.",
            configuration_schema={**Settings.model_json_schema(), "readOnly": True},
            setup_intents=("configure", "test"),
        )

    @asynccontextmanager
    async def open(self, context: Context) -> AsyncIterator[Controller]:
        yield NotionController(context)


class NotionController(RemoteMcp):
    def connection(self, configuration: ConnectorConfiguration) -> ResolvedMcpRecipe:
        return ResolvedMcpRecipe(
            server_id=configuration.connector_id,
            transport=McpRemoteTransport(
                url="https://mcp.notion.com/mcp",
                oauth=McpOAuthConfiguration(),
            ),
        )

    async def begin_setup(self, intent: str) -> SetupFlow:
        return OAuthSetup(self, "Notion", "Review the workspace and permissions on Notion's consent page.")
