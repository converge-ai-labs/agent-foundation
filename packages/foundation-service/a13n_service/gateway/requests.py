"""Native HTTP Run schemas and presence normalization at the transport boundary."""

from __future__ import annotations

from a13n_service.environments.domain import EnvironmentSelection
from a13n_service.environments.selection import Omitted
from a13n_service.interactions.command_values import (
    ContinueRunCommand,
    ContinueRunIntent,
    ForkRunCommand,
    ForkRunIntent,
    RetryRunCommand,
    StartRunCommand,
    StartRunIntent,
)


class StartRunRequest(StartRunIntent):
    environment: EnvironmentSelection | None = None

    def to_command(self) -> StartRunCommand:
        return StartRunCommand.model_validate(_command_values(self))


class ContinueRunRequest(ContinueRunIntent):
    environment: EnvironmentSelection | None = None

    def to_command(self) -> ContinueRunCommand:
        return ContinueRunCommand.model_validate(_command_values(self))


class ForkRunRequest(ForkRunIntent):
    environment: EnvironmentSelection | None = None

    def to_command(self) -> ForkRunCommand:
        return ForkRunCommand.model_validate(_command_values(self))


class RetryRunRequest(RetryRunCommand):
    pass


def _command_values(request: StartRunRequest | ContinueRunRequest | ForkRunRequest) -> dict[str, object]:
    values = request.model_dump(mode="python")
    if "environment" not in request.model_fields_set:
        values["environment"] = Omitted.UNSET
    return values
