"""Kubernetes is offered only for explicitly trusted operator cluster profiles."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Self

from a13n_harness.providers.environment.kubernetes.configuration import (
    ClusterProfile,
    DNSLabel,
    KubernetesConnectionConfiguration,
)
from a13n_harness.providers.environment.kubernetes.provider import KUBERNETES as EXECUTION
from a13n_harness.providers.environment.kubernetes.runtime import KubernetesRuntime, open_runtime
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.providers.environment.definition import EnvironmentManagementDefinition, management_definition
from a13n_service.providers.environment.recipes.kubernetes import KubernetesRecipe

from .management import KubernetesManagement


async def open_provider(
    *, configuration: KubernetesConnectionConfiguration, credential: BaseModel | None, runtime: KubernetesRuntime | None
) -> KubernetesManagement:
    return KubernetesManagement(
        runtime or await open_runtime(configuration=configuration, credential=credential), owns_runtime=runtime is None
    )


KUBERNETES = management_definition(
    EXECUTION, environment_model=KubernetesRecipe, provider_factory=open_provider, supports_destroy=True
)


def kubernetes(profiles: Mapping[str, ClusterProfile]) -> EnvironmentManagementDefinition:
    class Account(KubernetesConnectionConfiguration):
        cluster: str = Field(
            min_length=1,
            max_length=128,
            description="Operator-configured profile. Available profiles: " + ", ".join(profiles),
            json_schema_extra={"enum": list(profiles)},
        )
        namespace: DNSLabel = Field(
            description="Allowed namespaces by profile: "
            + "; ".join(f"{name}: {', '.join(profile.namespaces)}" for name, profile in profiles.items())
        )

        @model_validator(mode="after")
        def trusted_profile(self) -> Self:
            profile = profiles.get(self.cluster)
            if profile is None or self.namespace not in profile.namespaces:
                raise ValueError("Select an operator-approved cluster profile and namespace")
            self._cluster = profile
            return self

    class Recipe(KubernetesRecipe):
        model_config = ConfigDict(
            json_schema_extra={
                "x-environment-presets": {
                    connection: {
                        "execution_container": "envd",
                        "connection": connection,
                        "pod_template": {
                            "spec": {
                                "containers": [{"name": "envd", "image": "a13n-sandbox:local"}],
                                "restartPolicy": "Never",
                            }
                        },
                    }
                    for connection in ("direct", "port_forward")
                }
            }
        )

    return replace(
        KUBERNETES,
        configuration_model=Account,
        environment_model=Recipe,
        execution=replace(EXECUTION, configuration_model=Account),
    )
