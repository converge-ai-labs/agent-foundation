"""Publish and atomically select complete trusted Agent UI snapshot sets."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from a13n_ui.configuration.models import LoadedAgentUiConfiguration
from a13n_ui.storage import LocalStore, ObjectKind, ObjectRef

from .resolver import AgentCompositionResolver, ResolvedConfiguration


@dataclass(frozen=True, slots=True)
class AcceptedComposition:
    """Detached result of one successful accepted-configuration selection."""

    source_digest: str
    snapshots: MappingProxyType[tuple[Literal["agent", "environment"], str], ObjectRef]
    default_agent: str | None
    default_environment: str


class CompositionAcceptanceService:
    """Resolve, publish all immutable objects, then select them in one SQLite transaction."""

    def __init__(self, store: LocalStore, resolver: AgentCompositionResolver) -> None:
        self._store = store
        self._resolver = resolver

    async def accept(
        self,
        source: LoadedAgentUiConfiguration,
        *,
        expected_current_digest: str | None,
        restart_required: bool = False,
    ) -> AcceptedComposition:
        """Accept one coherent source candidate without disturbing the previous head on failure."""

        resolved = self._resolver.resolve(source)
        snapshots = await self._publish_snapshots(resolved)
        await self._store.configurations.accept(
            source_digest=source.source_digest,
            yaml_digest=source.yaml_digest,
            document=source.model_dump(mode="json"),
            snapshots=snapshots,
            restart_required=restart_required,
            expected_current_digest=expected_current_digest,
        )
        return AcceptedComposition(
            source_digest=source.source_digest,
            snapshots=MappingProxyType(dict(snapshots)),
            default_agent=resolved.default_agent,
            default_environment=resolved.default_environment,
        )

    async def _publish_snapshots(
        self,
        resolved: ResolvedConfiguration,
    ) -> dict[tuple[Literal["agent", "environment"], str], ObjectRef]:
        snapshots: dict[tuple[Literal["agent", "environment"], str], ObjectRef] = {}
        for name, snapshot in sorted(resolved.agents.items()):
            envelope = await self._store.objects.publish_model(
                object_kind=ObjectKind.agent_snapshot,
                value=snapshot,
            )
            snapshots[("agent", name)] = envelope.ref
        for name, snapshot in sorted(resolved.environments.items()):
            envelope = await self._store.objects.publish_model(
                object_kind=ObjectKind.environment_snapshot,
                value=snapshot,
            )
            snapshots[("environment", name)] = envelope.ref
        return snapshots


__all__ = ["AcceptedComposition", "CompositionAcceptanceService"]
