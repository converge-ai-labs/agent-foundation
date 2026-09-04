"""One offline Environment used by the runnable conversation example."""

from pathlib import Path

from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)


def create_demo_environment(workspace: Path) -> DirectLocalEnvironment:
    """Construct one fresh Direct Local Environment for a Harness turn."""

    workspace.mkdir(parents=True, exist_ok=True)
    provider = DirectLocalEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value=DirectLocalProviderConfiguration(
            root=DirectLocalRootConfiguration(path=workspace),
        ).model_dump(mode="json"),
    )
    environment = provider.create_environment(
        configuration=configuration,
        environment_id="agent-app-demo",
        state=None,
    )
    if not isinstance(environment, DirectLocalEnvironment):
        raise TypeError("Direct Local Provider returned an unexpected Environment")
    return environment


__all__ = ["create_demo_environment"]
