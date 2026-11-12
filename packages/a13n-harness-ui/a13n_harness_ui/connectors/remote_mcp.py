"""Reusable native Remote MCP tools and services for concrete Connector providers."""

from __future__ import annotations

from abc import abstractmethod
from contextlib import asynccontextmanager

from fastmcp import Client
from pydantic import JsonValue

from a13n_harness_ui.composition.models import ResolvedMcpRecipe
from a13n_harness_ui.configuration.models import McpPrivateValueSource
from a13n_harness_ui.connector_models import ConnectorConfiguration
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_adapters import HarnessUiMCP, prepare_mcp_transport

from .models import Capture, Status
from .sdk import Context, Controller


class RemoteMcp(Controller):
    """Provider-owned recipes over the existing native MCP execution path.

    Subclasses own their declaration, configuration and setup. This library is
    not a discoverable Connector type and adds no generic MCP configuration UI.
    """

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.mcp = RemoteMcpService(self)

    @abstractmethod
    def connection(self, configuration: ConnectorConfiguration) -> ResolvedMcpRecipe | None:
        """Return a safe recipe, or None until the concrete account is configured.

        Headers use McpPrivateValueSource references into the bound account's
        credentials. Recipes must never contain credential values.
        """
        ...

    def services(self) -> dict[tuple[str, int], object]:
        return {("mcp", 1): self.mcp}

    async def status(self) -> Status:
        current = await self.context.configuration()
        if not current.enabled:
            return Status(state="disabled", summary="Connection is disabled.")
        ready = self.connection(current) is not None
        return Status(
            state="ready" if ready else "unconfigured",
            summary="Configured. Upstream access is checked when connecting."
            if ready
            else "Complete connection setup to use its tools.",
        )

    async def capture(self) -> dict[str, JsonValue] | None:
        current = await self.context.configuration()
        recipe = self.connection(current) if current.enabled else None
        return {"mcp": recipe.model_dump(mode="json")} if recipe is not None else None

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


class RemoteMcpService:
    """Typed programmatic MCP access without exporting account credentials."""

    def __init__(self, controller: RemoteMcp) -> None:
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
