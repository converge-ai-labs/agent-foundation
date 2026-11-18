"""Fixed-target Envd execution through a Cloudflare environment gateway."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from a13n_harness.environment.attachments import EIPSessionBinding
from a13n_harness.environment.envd_settings import EnvdSettings
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState, decode_state_envelope
from a13n_harness.environment.session_recovery import EnvdSessionBorrower
from a13n_harness.environment.variables import EnvironmentVariables
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.eip.binding import configured_descriptor
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.managed import managed_http_runtime
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from a13n_service.providers.environment.cloudflare import Account

KEY = "cloudflare"
DIRECTORY = "/workspace"
ROUTE_HEADER = "x-a13n-environment"


class Recipe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: EnvironmentVariables = Field(default_factory=dict)
    envd: EnvdSettings = Field(default_factory=EnvdSettings, exclude_if=lambda value: not value.overrides())
    execution: Literal["envd_http", "envd_ws"] = Field(default="envd_http", title="Envd connection")
    network_egress: Literal["public", "disabled"] = Field(
        default="public",
        title="Container network egress",
        description="Public enables native internet connectivity. Disabled blocks workload internet access and preserves Envd control connectivity.",
    )


class Target(BaseModel):
    environment_id: str
    execution: Literal["envd_http", "envd_ws"]


def identity(*, configuration: Recipe, state: EnvironmentState | None) -> str:
    target = decode_state_envelope(KEY, state, Target)
    if target.execution != configuration.execution:
        raise provider_error(KEY, "provider_state_mismatch", Category.CONFLICT)
    return target.environment_id


def describe(configuration: Recipe) -> EnvironmentDescriptor:
    return configured_descriptor(working_directory=DIRECTORY)


@dataclass(frozen=True, slots=True)
class Gateway:
    account: Account
    secret: SecretStr = field(repr=False)

    def route(self, environment_id: str) -> dict[str, str]:
        return {ROUTE_HEADER: environment_id}

    @asynccontextmanager
    async def session(
        self,
        environment_id: str,
        *,
        session_recovery: EnvdSessionBorrower | None = None,
    ) -> AsyncIterator[EIPSessionBinding]:
        async def credential() -> HttpEnvdCredential:
            return HttpEnvdCredential(token=self.secret, headers={ROUTE_HEADER: SecretStr(environment_id)})

        async with managed_http_runtime(
            provider_key=KEY,
            target_id=environment_id,
            connection=HttpEnvdConnectionConfiguration(endpoint=self.account.endpoint),
            endpoint_policy=self.account._endpoint_policy,
            load_credential=credential,
        ) as runtime:
            async with runtime.open_session(
                expected_device_id=environment_id,
                required_methods=REQUIRED_METHODS,
                session_recovery=session_recovery,
                recovery_target=environment_id,
            ) as session:
                yield session


async def gateway(*, configuration: Account, credential: TokenCredential | None) -> Gateway:
    if credential is None:
        raise provider_error(KEY, "provider_credential_required", Category.INVALID)
    return Gateway(configuration, credential.api_key)


def construct(
    *,
    configuration: Recipe,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: Gateway | None,
    session_recovery: EnvdSessionBorrower | None = None,
) -> RemoteEnvdExecution:
    if runtime is None or state is None or identity(configuration=configuration, state=state) != environment_id:
        raise provider_error(KEY, "provider_state_mismatch", Category.CONFLICT)
    return RemoteEnvdExecution(
        environment=configuration.environment,
        provider_key=KEY,
        environment_id=environment_id,
        state=state,
        working_directory=DIRECTORY,
        session_context=runtime.session(environment_id, session_recovery=session_recovery),
    )


_factory = BackendFactory(
    key=KEY,
    environment_model=Recipe,
    execution=construct,
    describe=describe,
    runtime_factory=gateway,
    target_identity=identity,
)
EXECUTION = EnvironmentProviderDefinition(
    type=KEY,
    display_name="Cloudflare Containers",
    configuration_model=Account,
    credential_model=TokenCredential,
    environment_model=Recipe,
    connector_factory=_factory.connector,
    describe_environment=describe,
    target_identity=identity,
)
