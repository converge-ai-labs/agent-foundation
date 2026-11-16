"""Linear's official Remote MCP with selectable read/write or read-only access."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpOAuthConfiguration, McpRemoteTransport
from a13n_harness_ui.connector_models import ConnectorConfiguration

from .models import Declaration
from .oauth_mcp import OAuthSetup
from .remote_mcp import RemoteMcp
from .sdk import Context, Controller, Definition, SetupFlow


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access: Literal["read_only", "read_write"] = Field(
        default="read_write", title="Access", description="Changing access requires reconnecting."
    )


class Linear(Definition):
    configuration_model = Settings

    def declaration(self) -> Declaration:
        return Declaration(
            key="linear",
            name="Linear",
            brand="linear",
            description="Issues, projects and teams. Read and write by default.",
            configuration_schema=Settings.model_json_schema(),
            setup_intents=("configure", "test"),
        )

    @asynccontextmanager
    async def open(self, context: Context) -> AsyncIterator[Controller]:
        yield LinearController(context)


class LinearController(RemoteMcp):
    def connection(self, configuration: ConnectorConfiguration) -> ResolvedMcpRecipe:
        readonly = Settings.model_validate(configuration.settings).access == "read_only"
        return ResolvedMcpRecipe(
            server_id=configuration.connector_id,
            transport=McpRemoteTransport(
                url="https://mcp.linear.app/mcp/readonly" if readonly else "https://mcp.linear.app/mcp",
                oauth=McpOAuthConfiguration(scope="read" if readonly else None),
            ),
        )

    async def begin_setup(self, intent: str) -> SetupFlow:
        settings = Settings.model_validate((await self.context.configuration()).settings)
        permissions = (
            "Read-only access to Linear issues, projects and teams."
            if settings.access == "read_only"
            else "Read and write access to Linear. Review the permissions before authorizing."
        )
        return OAuthSetup(self, "Linear", permissions)
