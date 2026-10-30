from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment._backend import BackendReference
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache

from .desktop_access import Reply, Request
from .metrics import DeviceObservation, MetricsLaunch, ScrapeTarget


class ManagementBackend(BackendReference):
    """Lifecycle and desktop-access authority, without execution operations."""

    def _cache_state(self, state: EnvironmentState | None) -> None:
        self._known_state = state.model_copy(deep=True) if state is not None else None

    def _unsupported(self) -> Exception:
        return provider_error(
            self.provider_key, "provider_operation_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED
        )

    def validate_management(self) -> None:
        """Validate saved ownership before any management dispatch."""
        return None

    def configure_metrics(self, metrics: MetricsLaunch) -> None:
        """Accept private exporter settings only on supported creation paths."""
        raise self._unsupported()

    async def scrape_target(self) -> ScrapeTarget:
        """Resolve the owned HTTP route and credential without a Session or lifecycle mutation."""
        raise self._unsupported()

    async def observe_device(self, *, credential_cache: ManagedCredentialCache | None = None) -> DeviceObservation:
        """Read Envd-owned cached observations without an execution Session."""
        raise self._unsupported()

    async def create(self) -> None:
        raise self._unsupported()

    async def start(self) -> None:
        raise self._unsupported()

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        raise self._unsupported()

    async def stop(self) -> None:
        raise self._unsupported()

    async def destroy(self) -> None:
        raise self._unsupported()

    @property
    def keepalive_horizon(self) -> timedelta:
        return timedelta(seconds=300)

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime | None:
        raise self._unsupported()

    async def close(self) -> None:
        return None

    async def desktop_access(self, request: Request) -> Reply:
        """Management-only endpoint grants; never part of execution operations."""
        raise self._unsupported()
