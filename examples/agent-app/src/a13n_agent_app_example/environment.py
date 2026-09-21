"""One offline Environment used by the runnable conversation example."""

from pathlib import Path

from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL, DirectLocalEnvironment


def create_demo_environment(workspace: Path) -> DirectLocalEnvironment:
    """Construct one fresh Direct Local Environment for a Harness turn."""

    workspace.mkdir(parents=True, exist_ok=True)
    provider = DIRECT_LOCAL
    configuration = provider.validate_environment(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=workspace),
        ).model_dump(mode="json"),
    )
    environment = provider.construct(
        operation_id="agent-app-demo",
        allow_create=False,
        configuration=configuration,
        environment_id="agent-app-demo",
        state=None,
        runtime=None,
    )
    if not isinstance(environment, DirectLocalEnvironment):
        raise TypeError("Direct Local Provider returned an unexpected Environment")
    return environment


__all__ = ["create_demo_environment"]
