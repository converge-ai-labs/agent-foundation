"""Docker lifecycle configuration owned by Service."""

from typing import Literal

from a13n_harness.providers.environment.docker.configuration import DockerEnvironmentConfiguration


class DockerRecipe(DockerEnvironmentConfiguration):
    pull_policy: Literal["never", "if_missing"] = "if_missing"
