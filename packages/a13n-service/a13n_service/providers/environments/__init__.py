"""The environment provider types the Service offers, each qualified individually.

`docker` is the managed backend under the operator's engine and host directory choices, and `e2b` reaches the
E2B cloud only. `daytona`, `modal`, `vercel`, `sprites` and `runloop` are the Harness's own: their accounts name
an organization, team or app at a fixed vendor API, never an endpoint. `local` is a development-only managed
directory on the worker host. External envd targets are no provider type (`providers.envd`).
"""

from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

from a13n_environment.daytona.provider import DAYTONA
from a13n_environment.modal.provider import MODAL
from a13n_environment.runloop.provider import RUNLOOP
from a13n_environment.sprites.provider import SPRITES
from a13n_environment.vercel.provider import VERCEL

from a13n_service.providers.environments.docker import DOCKER, docker
from a13n_service.providers.environments.e2b import E2B
from a13n_service.providers.environments.local import LOCAL
from a13n_service.providers.registry import RegisteredProvider

BUILT_IN_ENVIRONMENT_PROVIDERS = (DOCKER, E2B, DAYTONA, MODAL, VERCEL, SPRITES, RUNLOOP, LOCAL)


def offered(
    definitions: Iterable[RegisteredProvider],
    *,
    allow_local: bool,
    docker_host: str | None,
    docker_mount_roots: Sequence[PurePosixPath],
) -> tuple[RegisteredProvider, ...]:
    """The definitions a deployment registers: `local` is no isolation boundary, so only development offers it,
    and `docker` follows the operator's engine and host directory choices."""
    configured = docker(host=docker_host, mount_roots=docker_mount_roots)
    return tuple(
        configured if definition is DOCKER else definition
        for definition in definitions
        if allow_local or definition is not LOCAL
    )
