"""Safe Connector domain failures."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import JsonValue


class ConnectorError(Exception):
    """A bounded failure that can cross an internal Connector boundary safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = MappingProxyType(dict(details or {}))


class ConnectorProviderError(ConnectorError):
    """A trusted Provider discovery, validation, or invocation failure."""


class ConnectorProviderCapabilityError(ConnectorProviderError):
    """A Provider operation was requested for an unsupported capability."""

    def __init__(self, capability: str) -> None:
        super().__init__(
            "The Connector Provider does not implement the required capability.",
            code="provider_capability_unavailable",
            details={"capability": capability},
        )


class ConnectorReauthorizationRequired(ConnectorProviderError):
    """Provider proved that the current credential grant is no longer usable."""

    def __init__(self) -> None:
        super().__init__(
            "The Connection requires reauthorization.",
            code="connection_reauthorization_required",
        )
