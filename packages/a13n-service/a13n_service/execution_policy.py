"""Service execution choices, layered at acceptance and frozen for the Run."""

from typing import Literal

from a13n_harness.capabilities.tool_execution import ToolExecutionConfig
from a13n_harness.environment.variables import EnvironmentVariables
from pydantic import BaseModel, ConfigDict, Field

type Steering = Literal["boundary", "immediate"]


class ToolExecutionOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["inline", "auto"] | None = None
    foreground_wait_seconds: float | None = Field(default=None, ge=0, le=300)


class ExecutionOverride(BaseModel):
    """Sparse choices: null inherits; Thread PATCH distinguishes omission from null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: EnvironmentVariables | None = Field(default=None, exclude_if=lambda value: not value)
    tools: ToolExecutionOverride | None = None
    steering: Steering | None = None

    def patch(self, changes: "ExecutionOverride") -> "ExecutionOverride":
        values = self.model_dump(exclude_none=True)
        for name in changes.model_fields_set:
            if name == "tools" and changes.tools is not None:
                tools = dict(values.get("tools", {}))
                tools.update(changes.tools.model_dump(exclude_unset=True))
                values[name] = tools
            else:
                values[name] = getattr(changes, name)
        return ExecutionOverride.model_validate(values)


class ExecutionPolicy(BaseModel):
    """Complete effective snapshot. Host defaults preserve Service's auto/immediate behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: EnvironmentVariables = Field(default_factory=dict, exclude_if=lambda value: not value)
    tools: ToolExecutionConfig = Field(default_factory=lambda: ToolExecutionConfig(mode="auto"))
    steering: Steering = "immediate"

    def overlay(self, *layers: ExecutionOverride | None) -> "ExecutionPolicy":
        tools = self.tools.model_dump()
        steering = self.steering
        environment = dict(self.environment)
        for layer in layers:
            if layer is None:
                continue
            if layer.environment is not None:
                environment.update(layer.environment)
            if layer.tools is not None:
                tools.update(layer.tools.model_dump(exclude_none=True))
            if layer.steering is not None:
                steering = layer.steering
        return ExecutionPolicy(
            tools=ToolExecutionConfig.model_validate(tools), steering=steering, environment=environment
        )

    def override(self) -> ExecutionOverride:
        return ExecutionOverride.model_validate(self.model_dump())


def matches(snapshot: ExecutionOverride | None, choices: ExecutionOverride | None) -> bool:
    """A steer may restate individual frozen leaves, but cannot change them."""
    accepted = ExecutionPolicy().overlay(snapshot)
    return accepted.overlay(choices) == accepted
