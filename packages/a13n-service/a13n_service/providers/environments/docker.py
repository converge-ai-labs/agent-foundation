"""Docker as the Service offers it: operator trust, since an engine runs containers as root on its host.

An account that names no engine uses the operator's, `environments.docker_host`, by default the Service process's
own Docker environment. A tenant may name only a remote engine over TCP or HTTPS, which the outbound endpoint
policy checks before every dial; a unix socket or SSH would reach a host the operator owns. A recipe binds host
directories only below `environments.docker_mount_roots`. The recipe type offers no other way out of the
container: it has no privileged mode, capabilities, devices, host namespaces, security options, volumes or runtime
selection, and refuses unknown fields. What the shared worker buffers or runs for a container stays within the
Harness defaults; container CPU, memory and process limits bound the operator's engine, not the worker.
"""

from collections.abc import Sequence
from dataclasses import replace
from importlib.metadata import version
from pathlib import PurePosixPath
from typing import Annotated, Any

from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration as HarnessRecipe,
)
from a13n_harness.providers.environment.docker.configuration import DockerMountConfiguration
from a13n_harness.providers.environment.docker.provider import DOCKER as HARNESS_DOCKER
from a13n_harness.providers.environment.docker.provider import (
    DockerConnectionConfiguration as HarnessAccount,
)
from packaging.version import Version
from pydantic import Field, field_validator

from a13n_service.providers.endpoints import engine_endpoint

_MOUNTS = HarnessRecipe.model_fields["mounts"]


def default_image() -> str:
    """The Service release's companion image; source and development builds use the mutable dev tag."""
    installed = Version(version("a13n-service"))
    if installed.base_version == "0.0.0" or installed.is_devrelease:
        tag = "dev"
    else:
        if (
            installed.epoch
            or len(installed.release) != 3
            or installed.local is not None
            or installed.post is not None
            or (installed.pre is not None and (installed.pre[0] != "rc" or installed.pre[1] < 1))
        ):
            raise ValueError(f"No Docker environment image release for a13n-service {installed}")
        tag = installed.base_version
        if installed.pre is not None:
            tag += f"-rc.{installed.pre[1]}"
    return f"ghcr.io/converge-ai-labs/a13n-docker-environment:{tag}"


def _worker_bound(name: str) -> Any:
    """A recipe field the worker process buffers or runs by: positive and at most the Harness default."""
    default = HarnessRecipe.model_fields[name].default
    return Field(default=default, gt=0, le=default)


def docker(*, host: str | None, mount_roots: Sequence[PurePosixPath]) -> EnvironmentProviderDefinition:
    """The Docker definition under the operator's engine and host directory choices."""

    class DockerConnectionConfiguration(HarnessAccount):
        docker_host: str = Field(
            default_factory=lambda: (
                host or HarnessAccount.model_fields["docker_host"].get_default(call_default_factory=True)
            ),
            min_length=1,
            max_length=2048,
            description=(
                "A remote engine as tcp://host:port or https://host:port; leave empty for the operator's engine."
            ),
        )

        # Validation skips a default, so only an engine the account names must be remote.
        @field_validator("docker_host")
        @classmethod
        def remote_engine(cls, value: str) -> str:
            engine_endpoint(value)
            return value

    class DockerEnvironmentConfiguration(HarnessRecipe):
        image: Annotated[str, Field(min_length=1, max_length=1024)] = default_image()
        mounts: tuple[DockerMountConfiguration, ...] = Field(
            default=(),
            title=_MOUNTS.title,
            description=(
                f"{_MOUNTS.description} A host source must lie below a directory the operator allows (none by default)."
            ),
        )
        max_file_bytes: int = _worker_bound("max_file_bytes")
        max_query_entries: int = _worker_bound("max_query_entries")
        max_output_preview_bytes: int = _worker_bound("max_output_preview_bytes")
        max_output_bytes_per_stream: int = _worker_bound("max_output_bytes_per_stream")
        max_spool_bytes: int = _worker_bound("max_spool_bytes")
        max_concurrent_processes: int = _worker_bound("max_concurrent_processes")

        @field_validator("mounts")
        @classmethod
        def allowed_sources(cls, mounts: tuple[DockerMountConfiguration, ...]) -> tuple[DockerMountConfiguration, ...]:
            for mount in mounts:
                source = PurePosixPath(mount.source)
                if not any(source.is_relative_to(root) for root in mount_roots):
                    raise ValueError("Host mount sources must lie below a directory the operator allows")
            return mounts

    return replace(
        HARNESS_DOCKER,
        configuration_model=DockerConnectionConfiguration,
        environment_model=DockerEnvironmentConfiguration,
    )


# The operator's defaults: the process's own engine and no host directories.
DOCKER = docker(host=None, mount_roots=())
