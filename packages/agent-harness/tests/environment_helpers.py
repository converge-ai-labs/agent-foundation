from __future__ import annotations

from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalProviderRuntime,
)
from a13n_environment_provider.direct_local.provider import (
    _DirectLocalFilePolicy as DirectLocalFilePolicy,
)
from a13n_harness.environment.sources import _EnvironmentAdapterBinding

__all__ = [
    "DirectLocalEnvironmentProviderBinding",
    "DirectLocalFilePolicy",
]


class DirectLocalEnvironmentProviderBinding(_EnvironmentAdapterBinding):
    """Test-only bridge from legacy fixtures to the public Environment adapter input."""

    def __init__(
        self,
        configuration: DirectLocalProviderConfiguration,
        runtime: DirectLocalProviderRuntime | None = None,
    ) -> None:
        provider = DirectLocalEnvironmentProvider()
        environment = provider.create_environment(
            configuration=configuration,
            state=None,
            runtime=runtime,
        )
        super().__init__(environment)
