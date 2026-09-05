"""Native HTTP Run schemas and presence normalization at the transport boundary."""

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
        return StartRunCommand.model_validate(
            {
                **self.model_dump(mode="python", exclude={"environment"}),
                "environment": self.environment if "environment" in self.model_fields_set else Omitted.UNSET,
            }
        )


class ContinueRunRequest(ContinueRunIntent):
    environment: EnvironmentSelection | None = None

    def to_command(self) -> ContinueRunCommand:
        return ContinueRunCommand.model_validate(
            {
                **self.model_dump(mode="python", exclude={"environment"}),
                "environment": self.environment if "environment" in self.model_fields_set else Omitted.UNSET,
            }
        )


class ForkRunRequest(ForkRunIntent):
    environment: EnvironmentSelection | None = None

    def to_command(self) -> ForkRunCommand:
        return ForkRunCommand.model_validate(
            {
                **self.model_dump(mode="python", exclude={"environment"}),
                "environment": self.environment if "environment" in self.model_fields_set else Omitted.UNSET,
            }
        )


class RetryRunRequest(RetryRunCommand):
    def to_command(self) -> RetryRunCommand:
        return RetryRunCommand.model_validate(self.model_dump(mode="python"))
