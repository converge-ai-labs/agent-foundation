"""Narrow authority boundary for runner Plugin Runtime commands."""

from __future__ import annotations

from typing import Protocol

from a13n_service.iam import AuthenticatedActor

from .domain import Plugin, PluginTaskReceipt, PluginVersion


class PluginRuntimeCommandDispatcher(Protocol):
    """Accept commands only after durable Worker staging can be coordinated.

    Implementations own idempotent receipt persistence, final authorization and
    target revalidation, candidate resolution, all-serviceable-Worker staging,
    atomic catalog cutover, and caller-scoped receipt reads.
    """

    async def activate(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        plugin: Plugin,
        plugin_version: PluginVersion,
        idempotency_key: str,
    ) -> PluginTaskReceipt: ...

    async def deactivate(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        plugin: Plugin,
        idempotency_key: str,
    ) -> PluginTaskReceipt: ...

    async def get_receipt(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        operation_id: str,
    ) -> PluginTaskReceipt: ...
