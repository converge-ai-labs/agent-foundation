"""One offline Environment used by the runnable conversation example."""

from pathlib import Path

from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)


def create_demo_environment(workspace: Path) -> DirectLocalEnvironmentProvider:
    """Create the example's single local Environment Provider."""

    workspace.mkdir(parents=True, exist_ok=True)
    return DirectLocalEnvironmentProvider(
        DirectLocalProviderConfiguration(
            environment_id="agent-app-demo",
            root=DirectLocalRootConfiguration(path=workspace),
        )
    )


__all__ = ["create_demo_environment"]
