"""Built-in Environment Provider examples."""

from .application import (
    DEFAULT_EXAMPLE_DOCKER_IMAGE,
    DockerExampleResult,
    StatelessExampleResult,
    run_direct_local,
    run_docker,
    run_local_envd,
)

__all__ = [
    "DEFAULT_EXAMPLE_DOCKER_IMAGE",
    "DockerExampleResult",
    "StatelessExampleResult",
    "run_direct_local",
    "run_docker",
    "run_local_envd",
]
