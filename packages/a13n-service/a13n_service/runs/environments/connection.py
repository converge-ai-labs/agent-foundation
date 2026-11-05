"""Resolve execution inputs from the published instance, without database I/O."""

from dataclasses import dataclass
from typing import Literal

from a13n_harness.providers.environment.execution_mode import ExecutionMode, execution_mode, mode_capabilities

from a13n_service.providers.environment.definition import EnvironmentManagementDefinition
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.schemas import EnvironmentMount


@dataclass(frozen=True, slots=True)
class ConnectionPlan:
    mode: ExecutionMode
    working_directory: str

    def mount(self, mount: EnvironmentMount) -> EnvironmentMount:
        return mount.model_copy(update={"working_directory": self.working_directory})


def resolve(definition: EnvironmentManagementDefinition, handle: Handle, mount: EnvironmentMount) -> ConnectionPlan:
    recipe = definition.validate_environment(handle.recipe)
    directory = mount.working_directory
    if directory is None:
        directory = definition.describe_environment(recipe).working_directory
    return ConnectionPlan(execution_mode(recipe), directory)


type PreviewSupport = Literal["supported", "connection_unsupported", "not_configured"]


def preview_support(handle: Handle | None, provider_type: str | None) -> PreviewSupport:
    if (
        handle is None
        or not handle.recipe.get("desktop")
        or provider_type not in {"e2b", "modal", "daytona", "vercel", "sprites"}
    ):
        return "not_configured"
    # Persisted execution is explicit for outbound instances. Other omitted modes
    # all permit a standalone connector; provider defaults do not affect this check.
    mode = ExecutionMode(handle.recipe.get("execution", ExecutionMode.NATIVE))
    if not mode_capabilities(mode).standalone_preview:
        return "connection_unsupported"
    return "supported"
