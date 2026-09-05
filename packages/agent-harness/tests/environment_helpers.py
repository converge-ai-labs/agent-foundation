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
    """Construct a Direct Local adapter for Harness mount tests."""

    def __init__(
        self,
        configuration: DirectLocalProviderConfiguration,
        runtime: DirectLocalProviderRuntime | None = None,
        *,
        environment_id: str,
    ) -> None:
        provider = DirectLocalEnvironmentProvider()
        environment = provider.create_environment(
            configuration=configuration,
            environment_id=environment_id,
            state=None,
            runtime=runtime,
        )
        super().__init__(environment)
