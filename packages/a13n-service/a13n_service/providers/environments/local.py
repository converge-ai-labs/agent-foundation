"""Development-only managed local environments: one directory per environment under the template's root.

Files and commands run directly on the worker host under the Service's own account, so this is no isolation
boundary. The Service offers it only when `environments.allow_local` is set, and every process that executes
runs must share the host holding the directories.
"""

import asyncio
import re
import shutil
from pathlib import Path
from typing import Literal

from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL, DirectLocalEnvironment
from a13n_harness.providers.environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_harness.providers.environment.management import Environment, EnvironmentProviderConfiguration
from a13n_harness.providers.environment.models import EnvironmentState

from a13n_service.infra.ids import OBJECT_ID_PATTERN

LOCAL_TYPE = "local"
# The directory name is the Service environment ID, which can never traverse out of the root.
_ENVIRONMENT_ID = re.compile(OBJECT_ID_PATTERN)


class LocalEnvironment(DirectLocalEnvironment):
    """The recipe's root is a base directory; this environment owns `{root}/{environment_id}` exclusively."""

    def __init__(self, recipe: DirectLocalEnvironmentConfiguration, *, environment_id: str, managed: bool) -> None:
        if not _ENVIRONMENT_ID.fullmatch(environment_id):
            raise provider_error(LOCAL_TYPE, "provider_target_invalid", EnvironmentProviderErrorCategory.INVALID)
        self._directory = recipe.root.path / environment_id
        # Only a lifecycle operation may create the directory; opening an existing environment never does.
        self._managed = managed
        own = recipe.model_copy(update={"root": DirectLocalRootConfiguration(path=self._directory)})
        super().__init__(own, environment_id=environment_id)

    @property
    def provider_key(self) -> str:
        return LOCAL_TYPE

    async def _prepare(self, *, mount_id: str) -> None:
        if self._managed:
            await asyncio.to_thread(self._directory.mkdir, mode=0o700, parents=True, exist_ok=True)
        await super()._prepare(mount_id=mount_id)

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        return "running" if await asyncio.to_thread(self._directory.is_dir) else "absent"

    async def _destroy(self) -> None:
        await asyncio.to_thread(_remove, self._directory)


def _remove(directory: Path) -> None:
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass


def _construct(
    *,
    configuration: DirectLocalEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: object | None,
    operation_id: str,
    allow_create: bool,
) -> Environment:
    del runtime, operation_id
    if state is not None:
        raise provider_error(LOCAL_TYPE, "provider_state_invalid", EnvironmentProviderErrorCategory.INVALID)
    return LocalEnvironment(configuration, environment_id=environment_id, managed=allow_create)


LOCAL = EnvironmentProviderDefinition(
    type=LOCAL_TYPE,
    display_name="Local directory (development only)",
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=DirectLocalEnvironmentConfiguration,
    construct=_construct,
    describe_environment=DIRECT_LOCAL.describe_environment,
    supports_stop=True,
    supports_destroy=True,
)
