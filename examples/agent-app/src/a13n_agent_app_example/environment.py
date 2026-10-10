"""One offline Environment connector used by the conversation example."""

from dataclasses import dataclass
from pathlib import Path

from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.execution import EnvironmentConnector
from a13n_environment.models import EnvironmentDescriptor, EnvironmentState


@dataclass(frozen=True)
class PreparedSource:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


def create_demo_environment(workspace: Path) -> PreparedSource:
    """The Host creates its directory; Harness opens a fresh execution per Run."""
    workspace.mkdir(parents=True, exist_ok=True)
    return PreparedSource(
        DIRECT_LOCAL.execution_connector({"root": {"path": str(workspace)}}, environment_id="agent-app-demo")
    )


__all__ = ["create_demo_environment"]
