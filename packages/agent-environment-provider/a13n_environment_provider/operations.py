from __future__ import annotations

from dataclasses import dataclass

from .commands import ProviderPortOperations, ProviderProcessOperations, ProviderShellOperations
from .files import FileOperator
from .retention import ProviderOutputOperations


@dataclass(frozen=True, slots=True)
class EnvironmentOperations:
    """Provider-local semantic operation facets captured during entry."""

    files: FileOperator | None = None
    shell: ProviderShellOperations | None = None
    processes: ProviderProcessOperations | None = None
    ports: ProviderPortOperations | None = None
    outputs: ProviderOutputOperations | None = None
