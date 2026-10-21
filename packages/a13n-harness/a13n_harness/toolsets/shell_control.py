"""Transportable controls for an original live Shell resource.

The caller holds current Environment authorization across dispatch; the owner
keeps its native handle, controller and observation state.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.commands import ProcessIdentity
from a13n_harness.environment.models import EnvironmentAction


class ShellTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_id: str
    identity: ProcessIdentity
    backing_identity: str | None


class ShellControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["describe", "inspect", "wait", "input", "signal"]
    stdout_offset: int = Field(default=0, ge=0)
    stderr_offset: int = Field(default=0, ge=0)
    timeout_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    data: str = ""
    close_stdin: bool = False
    signal: Literal["interrupt", "terminate", "kill"] = "terminate"

    @property
    def actions(self) -> frozenset[EnvironmentAction]:
        match self.operation:
            case "describe":
                return frozenset()
            case "inspect":
                return frozenset({EnvironmentAction.PROCESS_INSPECT})
            case "wait":
                actions = {EnvironmentAction.PROCESS_INSPECT, EnvironmentAction.PROCESS_READ_OUTPUT}
                if self.timeout_seconds > 0:
                    actions.add(EnvironmentAction.PROCESS_WAIT)
            case "input":
                actions = {EnvironmentAction.PROCESS_INSPECT}
                if self.data:
                    actions.add(EnvironmentAction.PROCESS_WRITE_STDIN)
                if self.close_stdin:
                    actions.add(EnvironmentAction.PROCESS_CLOSE_STDIN)
            case "signal":
                actions = {
                    EnvironmentAction.PROCESS_KILL if self.signal == "kill" else EnvironmentAction.PROCESS_SIGNAL
                }
        return frozenset(actions)
