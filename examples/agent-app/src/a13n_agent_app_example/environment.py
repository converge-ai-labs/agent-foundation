"""One offline Environment connector used by the conversation example."""

from pathlib import Path

from a13n_environment import EnvironmentConnector
from a13n_environment.direct_local.provider import DIRECT_LOCAL


def create_demo_environment(workspace: Path) -> EnvironmentConnector:
    """The Host creates its directory; Harness opens a fresh execution per Run."""
    workspace.mkdir(parents=True, exist_ok=True)
    return DIRECT_LOCAL.execution_connector({"root": {"path": str(workspace)}}, environment_id="agent-app-demo")


__all__ = ["create_demo_environment"]
