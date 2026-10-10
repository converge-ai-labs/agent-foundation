"""Development-only management of per-environment directories on the worker host."""

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from a13n_environment import EnvironmentConnector, EnvironmentProvider, EnvironmentStatus
from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_environment.management import EnvironmentProviderConfiguration
from a13n_environment.models import EnvironmentState
from anyio import to_thread
from pydantic import BaseModel

from a13n_service.infra.ids import OBJECT_ID_PATTERN

LOCAL_TYPE = "local"
_ENVIRONMENT_ID = re.compile(OBJECT_ID_PATTERN)


def _recipe(
    environment: object, environment_id: str, state: EnvironmentState | None
) -> DirectLocalEnvironmentConfiguration:
    if not _ENVIRONMENT_ID.fullmatch(environment_id):
        raise provider_error(LOCAL_TYPE, "provider_target_invalid", EnvironmentProviderErrorCategory.INVALID)
    if state is not None:
        raise provider_error(LOCAL_TYPE, "provider_state_invalid", EnvironmentProviderErrorCategory.INVALID)
    recipe = DirectLocalEnvironmentConfiguration.model_validate(environment)
    return recipe.model_copy(update={"root": DirectLocalRootConfiguration(path=recipe.root.path / environment_id)})


class LocalProvider(EnvironmentProvider[DirectLocalEnvironmentConfiguration]):
    """Own directory lifecycle; Direct Local owns only executions inside existing directories."""

    async def create(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None = None
    ) -> EnvironmentState | None:
        directory = _recipe(environment, environment_id, state).root.path
        await to_thread.run_sync(lambda: directory.mkdir(mode=0o700, parents=True, exist_ok=True))
        return None

    async def start(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> EnvironmentState | None:
        status = await self.inspect(environment, environment_id=environment_id, state=state)
        if status.status == "absent":
            raise provider_error(LOCAL_TYPE, "provider_target_missing", EnvironmentProviderErrorCategory.MISSING)
        return None

    async def inspect(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentStatus:
        directory = _recipe(environment, environment_id, state).root.path
        exists = await to_thread.run_sync(directory.is_dir)
        return EnvironmentStatus("running" if exists else "absent", None)

    async def stop(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> EnvironmentState | None:
        _recipe(environment, environment_id, state)
        return None

    async def destroy(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> None:
        directory = _recipe(environment, environment_id, state).root.path
        await to_thread.run_sync(_remove, directory)

    def keepalive_horizon(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> timedelta:
        raise provider_error(LOCAL_TYPE, "provider_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED)

    async def keepalive(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        deadline: datetime,
        operation_id: str,
    ) -> datetime | None:
        raise provider_error(LOCAL_TYPE, "provider_unsupported", EnvironmentProviderErrorCategory.UNSUPPORTED)

    def execution_connector(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentConnector:
        return DIRECT_LOCAL.execution_connector(
            _recipe(environment, environment_id, state), environment_id=environment_id
        )

    async def close(self) -> None:
        pass


def _remove(directory: Path) -> None:
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass


async def _provider(
    *, configuration: EnvironmentProviderConfiguration, credential: BaseModel | None, runtime: object | None
) -> EnvironmentProvider[DirectLocalEnvironmentConfiguration]:
    return LocalProvider()


def _connector(
    *,
    configuration: EnvironmentProviderConfiguration,
    credential: BaseModel | None,
    environment: DirectLocalEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: object | None,
) -> EnvironmentConnector:
    return LocalProvider().execution_connector(environment, environment_id=environment_id, state=state)


LOCAL = EnvironmentProviderDefinition(
    type=LOCAL_TYPE,
    display_name="Local directory (development only)",
    configuration_model=EnvironmentProviderConfiguration,
    environment_model=DirectLocalEnvironmentConfiguration,
    provider_factory=_provider,
    connector_factory=_connector,
    describe_environment=DIRECT_LOCAL.describe_environment,
    supports_stop=True,
    supports_destroy=True,
)
