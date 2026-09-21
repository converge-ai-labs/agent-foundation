from __future__ import annotations

from a13n_harness.environment.sources import EnvironmentMount, _EnvironmentAdapterBinding
from a13n_harness.providers.environment.direct_local.configuration import DirectLocalEnvironmentConfiguration
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.direct_local.provider import (
    _DirectLocalFilePolicy as DirectLocalFilePolicy,
)

__all__ = [
    "DirectLocalEnvironmentProviderBinding",
    "DirectLocalFilePolicy",
]


class DirectLocalEnvironmentProviderBinding(_EnvironmentAdapterBinding):
    """Construct a Direct Local adapter for Harness mount tests."""

    def __init__(
        self,
        configuration: DirectLocalEnvironmentConfiguration,
        *,
        environment_id: str,
    ) -> None:
        provider = DIRECT_LOCAL
        environment = provider.construct(
            operation_id="op-test",
            allow_create=True,
            configuration=configuration,
            environment_id=environment_id,
            state=None,
            runtime=None,
        )
        super().__init__(EnvironmentMount(environment))
