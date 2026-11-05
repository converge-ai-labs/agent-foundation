"""Execution transport selection and transport-level capabilities.

Callers pass validated recipes so provider defaults are applied by their owner.
Provisioning remains provider-specific; a reverse connection is owned by the Host.
"""

from dataclasses import dataclass
from enum import StrEnum


class ExecutionMode(StrEnum):
    NATIVE = "native"
    ENVD_HTTP = "envd_http"
    ENVD_WS = "envd_ws"


@dataclass(frozen=True, slots=True)
class ExecutionCapabilities:
    host_connection: bool
    standalone_preview: bool


_CAPABILITIES = {
    ExecutionMode.NATIVE: ExecutionCapabilities(False, True),
    ExecutionMode.ENVD_HTTP: ExecutionCapabilities(False, True),
    ExecutionMode.ENVD_WS: ExecutionCapabilities(True, False),
}


def execution_mode(recipe: object) -> ExecutionMode:
    return ExecutionMode(getattr(recipe, "execution", ExecutionMode.NATIVE))


def mode_capabilities(mode: ExecutionMode) -> ExecutionCapabilities:
    return _CAPABILITIES[mode]


def capabilities(recipe: object) -> ExecutionCapabilities:
    return mode_capabilities(execution_mode(recipe))
