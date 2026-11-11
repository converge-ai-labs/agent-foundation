"""Remote MCP as a Connector, using the existing native MCP transport and Toolset."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastmcp import Client
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpPrivateValueSource, McpRemoteTransport
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_adapters import HarnessUiMCP, prepare_mcp_transport

from .models import Capture, Declaration, SetupAction, Status, Step
from .sdk import Context, Controller, Definition, SetupFlow

GITHUB_ENDPOINT = "https://api.githubcopilot.com/mcp/"


class RemoteSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = ""
    header_names: tuple[str, ...] = ()
    tools: tuple[str, ...] | None = Field(
        default=None, description="Omit to allow all upstream tools; an empty list allows none."
    )
    init_timeout_seconds: float = Field(default=30, gt=0, le=300)
    protocol: Literal["auto", "legacy", "2026-07-28"] = "auto"

    @model_validator(mode="after")
    def valid(self):
        if self.url:
            McpRemoteTransport(
                url=self.url,
                init_timeout_seconds=self.init_timeout_seconds,
                headers={
                    name: McpPrivateValueSource(connector_id="validation", configuration_version=1, key=name)
                    for name in self.header_names
                },
            )
        if self.protocol not in {"auto", "legacy", "2026-07-28"}:
            raise ValueError("Unsupported MCP protocol")
        if len(set(name.lower() for name in self.header_names)) != len(self.header_names):
            raise ValueError("Header names must be unique")
        if any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) for name in self.header_names):
            raise ValueError("Invalid HTTP header name")
        return self


class HeaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: str = Field(
        default="",
        json_schema_extra={"writeOnly": True},
        description="Leave blank to retain the saved value for this header.",
    )


class RemoteForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(
        title="Remote MCP endpoint", description="HTTPS URL, or plaintext literal loopback for local development."
    )
    headers: list[HeaderInput] = Field(
        default_factory=list, description="Private HTTP headers. Remove a row to clear that header."
    )
    tools: list[str] | None = Field(
        default=None, description="All upstream tools when unset; an empty list disables every tool."
    )
    init_timeout_seconds: float = Field(default=30, gt=0, le=300, title="Initialization timeout (seconds)")


class GitHubForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(
        default="",
        title="GitHub personal access token",
        json_schema_extra={"writeOnly": True},
        description="Stored only in this instance's private database. Leave blank to keep the current token.",
    )
    clear_token: bool = Field(default=False, title="Clear the saved token")
    tools: list[str] | None = Field(
        default=None, description="All upstream tools when unset; an empty list disables every tool."
    )
    init_timeout_seconds: float = Field(default=30, gt=0, le=300, title="Initialization timeout (seconds)")


class RemoteMcp(Definition):
    configuration_model = RemoteSettings
    key = "remote-mcp"
    name = "Remote MCP"
    description = "Connect any Streamable HTTP MCP server with private authentication headers and native tools."
    form_model = RemoteForm

    def declaration(self) -> Declaration:
        return Declaration(
            key=self.key,
            name=self.name,
            description=self.description,
            configuration_schema=RemoteSettings.model_json_schema(),
        )

    @asynccontextmanager
    async def open(self, context: Context) -> AsyncIterator[Controller]:
        yield RemoteController(context, self)


class GitHub(RemoteMcp):
    key = "github"
    name = "GitHub"
    description = "Use GitHub's official remote MCP server with your personal access token. Tool access follows your GitHub permissions."
    form_model = GitHubForm


class RemoteController(Controller):
    def __init__(self, context: Context, definition: RemoteMcp) -> None:
        super().__init__(context)
        self.definition = definition
        self._service = RemoteMcpService(self)

    def services(self) -> dict[tuple[str, int], object]:
        return {("mcp", 1): self._service}

    async def status(self) -> Status:
        current = await self.context.configuration()
        if not current.enabled:
            return Status(state="disabled", summary="Connection is disabled.")
        settings = RemoteSettings.model_validate(current.settings)
        ready = bool(settings.url) and all(current.credentials.get(name) for name in settings.header_names)
        if self.definition.key == "github":
            ready = ready and bool(current.credentials.get("Authorization"))
        return Status(
            state="ready" if ready else "unconfigured",
            summary="Configured. Upstream access is checked when connecting."
            if ready
            else "Complete connection setup to use its tools.",
        )

    async def model_context(self) -> dict[str, JsonValue]:
        current = await self.context.configuration()
        settings = RemoteSettings.model_validate(current.settings)
        status = await self.status()
        return {
            "state": status.state,
            "summary": status.summary,
            "endpoint": settings.url,
            "tools": list(settings.tools) if settings.tools is not None else None,
        }

    async def capture(self) -> dict[str, JsonValue] | None:
        if (await self.status()).state != "ready":
            return None
        current = await self.context.configuration()
        settings = RemoteSettings.model_validate(current.settings)
        recipe = ResolvedMcpRecipe(
            server_id=current.connector_id,
            transport=McpRemoteTransport(
                url=settings.url,
                init_timeout_seconds=settings.init_timeout_seconds,
                headers={
                    name: McpPrivateValueSource(
                        connector_id=current.connector_id, configuration_version=current.version, key=name
                    )
                    for name in settings.header_names
                },
            ),
            tools=settings.tools,
            protocol=settings.protocol,
        )
        return {"mcp": recipe.model_dump(mode="json")}

    async def _private_value(self, source: McpPrivateValueSource) -> str:
        if source.connector_id != self.context.connector_id:
            raise HarnessUiError("Private MCP source belongs to another Connector.", code="connector_binding_changed")
        current = await self.context.require_available()
        if current.version != source.configuration_version or not current.credentials.get(source.key):
            raise HarnessUiError(
                "Private MCP source is missing or its binding changed.", code="connector_binding_changed"
            )
        return current.credentials[source.key]

    def capability(self, capture: Capture) -> HarnessUiMCP:
        recipe = ResolvedMcpRecipe.model_validate(capture.binding["mcp"])

        async def guard() -> None:
            await self.context.require_available(capture)

        return HarnessUiMCP(recipe, private_values=self._private_value, operation_guard=guard)

    async def begin_setup(self, intent: str) -> SetupFlow:
        return RemoteSetup(self)


class RemoteMcpService:
    """Shared programmatic MCP access; credentials stay inside the provider boundary."""

    def __init__(self, controller: RemoteController) -> None:
        self._controller = controller

    @asynccontextmanager
    async def _client(self):
        expected = await self._controller.context.require_available()
        binding = await self._controller.capture()
        if binding is None:
            raise HarnessUiError("Complete connection setup first.", code="connector_service_unavailable")
        recipe = ResolvedMcpRecipe.model_validate(binding["mcp"])
        transport, _, timeout = await prepare_mcp_transport(
            recipe, None, private_values=self._controller._private_value
        )
        async with Client(transport, mode=recipe.protocol, init_timeout=timeout) as client:
            # Recheck after connecting, before submitting a provider operation.
            current = await self._controller.context.require_available()
            if current.version != expected.version:
                raise HarnessUiError("Connection settings changed.", code="connector_binding_changed")
            yield client, recipe

    async def list_tools(self):
        async with self._client() as (client, recipe):
            return [tool for tool in await client.list_tools() if recipe.tools is None or tool.name in recipe.tools]

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]):
        async with self._client() as (client, recipe):
            if recipe.tools is not None and name not in recipe.tools:
                raise HarnessUiError("Tool is not enabled for this connection.", code="connector_tool_unavailable")
            return await client.call_tool(name, arguments)


class RemoteSetup(SetupFlow):
    def __init__(self, controller: RemoteController) -> None:
        self.controller = controller
        self._step: Step | None = None
        self._configuration_version: int | None = None

    async def read(self) -> Step:
        if self._step is not None:
            return self._step
        current = await self.controller.context.configuration()
        self._configuration_version = current.version
        settings = RemoteSettings.model_validate(current.settings)
        values: dict[str, JsonValue] = {
            "tools": list(settings.tools) if settings.tools is not None else None,
            "init_timeout_seconds": settings.init_timeout_seconds,
        }
        github = self.controller.definition.key == "github"
        if not github:
            values.update(
                {"url": settings.url, "headers": [{"name": name, "value": ""} for name in settings.header_names]}
            )
        actions = [
            SetupAction(id="save", label="Save connection"),
            SetupAction(id="test", label="Test saved connection"),
        ]
        if github:
            actions.insert(
                0,
                SetupAction(
                    id="guide",
                    label="Create a personal access token",
                    kind="open_url",
                    url="https://github.com/settings/personal-access-tokens",
                ),
            )
        self._step = Step(
            key="configure",
            title=f"Configure {self.controller.definition.name}",
            description=(
                "GitHub's official MCP server: "
                + GITHUB_ENDPOINT
                + " Token type, organization approval and repository permissions determine which tools work. Saving does not contact GitHub."
            )
            if github
            else "Header values are write-only. Saving does not contact the upstream server.",
            form=self.controller.definition.form_model.model_json_schema(),
            values=values,
            actions=tuple(actions),
        )
        return self._step

    async def act(self, action: str, values: dict[str, JsonValue]) -> Step:
        context = self.controller.context
        current = await context.configuration()
        if self._configuration_version is not None and current.version != self._configuration_version:
            raise HarnessUiError(
                "Connection settings changed; close this setup and start again.", code="connector_conflict"
            )
        if action == "test":
            binding = await self.controller.capture()
            if binding is None:
                raise ValueError("Save a complete connection before testing")
            recipe = ResolvedMcpRecipe.model_validate(binding["mcp"])
            transport, _, timeout = await prepare_mcp_transport(
                recipe, None, private_values=self.controller._private_value
            )
            try:
                async with Client(transport, mode=recipe.protocol, init_timeout=timeout) as client:
                    tools = await client.list_tools()
            except Exception as exc:
                raise HarnessUiError(
                    f"MCP connection test failed ({type(exc).__name__}). Check endpoint, credentials and upstream permissions.",
                    code="connector_connection_failed",
                ) from None
            self._step = (await self.read()).model_copy(
                update={"description": f"Connected. The server advertised {len(tools)} tools."}
            )
            return self._step
        if action != "save":
            raise ValueError("Unknown setup action")
        credentials = dict(current.credentials)
        if self.controller.definition.key == "github":
            form = GitHubForm.model_validate(values)
            if form.clear_token:
                credentials.pop("Authorization", None)
            elif form.token:
                if any(ord(char) < 32 or ord(char) == 127 for char in form.token):
                    raise ValueError("Token must contain no control characters")
                credentials["Authorization"] = f"Bearer {form.token}"
            settings = RemoteSettings(
                url=GITHUB_ENDPOINT,
                header_names=("Authorization",),
                tools=None if form.tools is None else tuple(form.tools),
                init_timeout_seconds=form.init_timeout_seconds,
            )
        else:
            form = RemoteForm.model_validate(values)
            credentials = {header.name: header.value or credentials.get(header.name, "") for header in form.headers}
            if any(
                not value or any(ord(char) < 32 or ord(char) == 127 for char in value) for value in credentials.values()
            ):
                raise ValueError("Every header needs a single-line value")
            settings = RemoteSettings(
                url=form.url,
                header_names=tuple(credentials),
                tools=None if form.tools is None else tuple(form.tools),
                init_timeout_seconds=form.init_timeout_seconds,
            )
        candidate = current.model_copy(update={"settings": settings.model_dump(mode="json")})
        await context.configure(candidate, expected_version=current.version, credentials=credentials)
        self._step = Step(
            key="saved",
            title="Connection saved",
            state="completed",
            description="The next deliberate Run can capture this connection's tools. No blocked operation was replayed.",
        )
        return self._step
