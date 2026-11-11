"""GitHub accounts backed by the official Remote MCP server."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpPrivateValueSource, McpRemoteTransport
from a13n_harness_ui.connector_models import ConnectorConfiguration
from a13n_harness_ui.errors import HarnessUiError

from .models import Declaration, SetupAction, Step
from .remote_mcp import RemoteMcp
from .sdk import Context, Controller, Definition, SetupFlow

GITHUB_ENDPOINT = "https://api.githubcopilot.com/mcp/"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CredentialsForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(
        default="",
        title="GitHub personal access token",
        json_schema_extra={"writeOnly": True},
        description="Stored only in this instance's private database. Leave blank to keep the current token.",
    )
    clear_token: bool = Field(default=False, title="Remove saved token")

    @model_validator(mode="after")
    def token_action(self):
        if self.clear_token and self.token:
            raise HarnessUiError(
                "Choose either a replacement token or removal, not both.", code="connector_input_invalid"
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in self.token):
            raise HarnessUiError("Token must contain no control characters.", code="connector_input_invalid")
        return self


class GitHub(Definition):
    configuration_model = Settings

    def declaration(self) -> Declaration:
        return Declaration(
            key="github",
            name="GitHub",
            description="Connect GitHub with your personal access token. Access follows your GitHub permissions.",
            configuration_schema={**Settings.model_json_schema(), "readOnly": True},
            setup_intents=("configure", "test"),
        )

    @asynccontextmanager
    async def open(self, context: Context) -> AsyncIterator[Controller]:
        yield GitHubController(context)


class GitHubController(RemoteMcp):
    def connection(self, configuration: ConnectorConfiguration) -> ResolvedMcpRecipe | None:
        if not configuration.credentials.get("token"):
            return None
        return ResolvedMcpRecipe(
            server_id=configuration.connector_id,
            transport=McpRemoteTransport(
                url=GITHUB_ENDPOINT,
                headers={
                    "Authorization": McpPrivateValueSource(
                        connector_id=configuration.connector_id,
                        configuration_version=configuration.version,
                        key="token",
                    )
                },
            ),
        )

    async def begin_setup(self, intent: str) -> SetupFlow:
        return GitHubSetup(self, intent=intent)


class GitHubSetup(SetupFlow):
    def __init__(self, controller: GitHubController, *, intent: str = "configure") -> None:
        self.controller = controller
        self.intent = intent
        self._step: Step | None = None
        self._configuration_version: int | None = None

    async def read(self) -> Step:
        if self._step is not None:
            return self._step
        current = await self.controller.context.configuration()
        self._configuration_version = current.version
        if self.intent == "test":
            self._step = await self._saved_step()
            return self._step
        schema = CredentialsForm.model_json_schema()
        if current.credentials.get("token"):
            schema["x-a13n-saved"] = ["/token"]
        else:
            schema["properties"].pop("clear_token")
        self._step = Step(
            key="configure",
            title="Connect GitHub",
            description="Choose token permissions for the repositories and organizations you want to use. Saving does not contact GitHub.",
            form=schema,
            actions=(
                SetupAction(
                    id="guide",
                    label="Create a personal access token",
                    kind="open_url",
                    url="https://github.com/settings/personal-access-tokens",
                ),
                SetupAction(id="save", label="Save connection"),
            ),
        )
        return self._step

    async def act(self, action: str, values: dict[str, JsonValue]) -> Step:
        context = self.controller.context
        current = await context.configuration()
        if self._configuration_version is not None and current.version != self._configuration_version:
            raise HarnessUiError(
                "Connection settings changed; close this setup and start again.", code="connector_conflict"
            )
        if action == "done":
            self._step = Step(key="completed", title="Connection saved", state="completed")
        elif action == "edit":
            self.intent = "configure"
            self._step = None
            return await self.read()
        elif action == "test":
            try:
                await self.controller.mcp.list_tools()
            except Exception:
                raise HarnessUiError(
                    "GitHub connection test failed. Check your token and GitHub permissions.",
                    code="connector_connection_failed",
                ) from None
            self._step = (await self.read()).model_copy(
                update={"description": "Connection test succeeded.", "error": None}
            )
        elif action == "save":
            form = CredentialsForm.model_validate(values)
            credentials = dict(current.credentials)
            if form.clear_token:
                credentials.pop("token", None)
            elif form.token:
                credentials["token"] = f"Bearer {form.token}"
            saved = await context.configure(current, expected_version=current.version, credentials=credentials)
            self._configuration_version = saved.version
            self._step = await self._saved_step()
        else:
            raise ValueError("Unknown setup action")
        return self._step

    async def _saved_step(self) -> Step:
        actions = [SetupAction(id="edit", label="Edit connection")]
        if (await self.controller.status()).state == "ready":
            actions.append(SetupAction(id="test", label="Test connection"))
        actions.append(SetupAction(id="done", label="Done"))
        return Step(
            key="saved",
            title="Connection saved",
            description="Send a new message to use it. No operation was replayed.",
            actions=tuple(actions),
        )
