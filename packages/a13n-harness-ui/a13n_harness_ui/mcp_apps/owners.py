"""Resolve current App authority through the owning root and its current child roster."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_harness.tools.permissions import ToolPermissionsCapability

from a13n_harness_ui.composition.auxiliary import capability_configuration
from a13n_harness_ui.composition.models import ResolvedAgentNode, ResolvedMcpRecipe, ResolvedRunComposition
from a13n_harness_ui.composition.resolver import AgentCompositionResolver, ThreadCompositionSelection
from a13n_harness_ui.composition.service import CompositionAcceptanceService
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.storage import LocalStore

from .connections import Connections


@dataclass(frozen=True)
class AppOwner:
    thread_id: str
    root_thread_id: str
    node: ResolvedAgentNode
    route: tuple[str, ...]

    def recipe(self, server_id: str) -> ResolvedMcpRecipe:
        recipe = next((item for item in self.node.mcp_servers if item.server_id == server_id), None)
        if recipe is None or not recipe.apps_enabled:
            raise HarnessUiError("This MCP server is not enabled for the current App owner.", code="mcp_app_denied")
        return recipe

    def require_tool(self, name: str) -> None:
        if self.node.tools is not None and name not in self.node.tools:
            raise HarnessUiError("This tool is excluded by the current Agent selection.", code="mcp_app_denied")


class CurrentOwners:
    def __init__(
        self, store: LocalStore, configurations: CompositionAcceptanceService, resolver: AgentCompositionResolver
    ) -> None:
        self.store = store
        self.configurations = configurations
        self.resolver = resolver

    async def resolve(self, thread_id: str) -> AppOwner:
        """Saved child compositions identify a route; they never supply today's policy."""
        source = await self.configurations.current()
        if source is None or not source.document.webui.mcp_apps.enabled:
            raise HarnessUiError("MCP Apps are disabled.", code="mcp_apps_disabled")
        thread = await self.store.threads.get(thread_id)
        route: list[str] = []
        seen: set[str] = set()
        while thread is not None:
            if thread.thread_id in seen or thread.memory_scope is not None or thread.archived:
                raise HarnessUiError("The App's owner is unavailable.", code="mcp_app_owner_unavailable")
            seen.add(thread.thread_id)
            if thread.parent_thread_id is None:
                break
            first = await self.store.child_executions.first_for_child(thread.thread_id)
            if first is None or first.parent_thread_id != thread.parent_thread_id:
                raise HarnessUiError("The App's child route is unavailable.", code="mcp_app_owner_unavailable")
            initial = await self.store.objects.read_model(first.run_composition, ResolvedRunComposition)
            if initial.thread_id != thread.thread_id:
                raise HarnessUiError("The App's child route is invalid.", code="mcp_app_owner_unavailable")
            route.append(initial.root.roster_name)
            thread = await self.store.threads.get(thread.parent_thread_id)
        if thread is None:
            raise HarnessUiError("The App's owning Thread is unavailable.", code="mcp_app_owner_unavailable")
        node = self.resolver.resolve_agent(source, ThreadCompositionSelection.from_thread(thread))
        for name in reversed(route):
            edge = next((item for item in node.children if item.name == name), None)
            if edge is None:
                raise HarnessUiError("The App's child route is no longer selected.", code="mcp_app_owner_unavailable")
            node = edge.definition
        return AppOwner(thread_id, thread.thread_id, node, tuple(reversed(route)))

    async def retire_unselected(self, connections: Connections) -> None:
        """Configuration changes also revoke connections owned by removed child routes."""
        for thread_id in {item.thread_id for item in connections.current()}:
            try:
                owner = await self.resolve(thread_id)
            except HarnessUiError:
                connections.retain_thread_servers(thread_id, set())
            else:
                connections.retain_thread_servers(
                    thread_id, {item.server_id for item in owner.node.mcp_servers if item.apps_enabled}
                )

    def permissions(self, owner: AppOwner) -> ToolPermissionsCapability:
        recipe = next(
            (item for item in owner.node.capabilities if item.capability == "ToolPermissionsCapability"), None
        )
        if recipe is None:
            return ToolPermissionsCapability()
        capabilities = self.resolver.catalog.capabilities(((recipe.capability, capability_configuration(recipe, {})),))
        capability = capabilities[0].capability
        if not isinstance(capability, ToolPermissionsCapability):
            raise HarnessUiError(
                "The App policy requires a Host-compatible permission adapter.", code="mcp_app_policy_unsupported"
            )
        return capability
