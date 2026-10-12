from __future__ import annotations

from dataclasses import dataclass

from a13n_harness.environment.commands import ProviderPortOperations, ProviderProcessOperations, ProviderShellOperations
from a13n_harness.environment.computer import ProviderComputerOperations
from a13n_harness.environment.files import FileOperator
from a13n_harness.environment.retention import ProviderOutputOperations


@dataclass(frozen=True, slots=True)
class EnvironmentOperations:
    """Provider-local semantic operation facets captured during entry."""

    files: FileOperator | None = None
    shell: ProviderShellOperations | None = None
    processes: ProviderProcessOperations | None = None
    ports: ProviderPortOperations | None = None
    outputs: ProviderOutputOperations | None = None
    computer: ProviderComputerOperations | None = None
