"""Cloudflare Containers execution uses the existing Envd/EIP HTTP carrier."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from a13n_envd_client import EIPSession
from pydantic import SecretStr

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import (
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentDescriptor,
    EnvironmentPermissionSet,
    EnvironmentState,
    decode_target_state,
)
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment.cloudflare.shared import (
    CloudflareConnectionConfiguration,
    CloudflareEnvironmentConfiguration,
    CloudflareReference,
    CloudflareState,
    bridge_token,
)
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.eip.binding import configured_descriptor
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.managed import managed_http_runtime


async def runtime(
    *, configuration: CloudflareConnectionConfiguration, credential: TokenCredential | None
) -> NativeRuntime:
    if credential is None:
        raise ValueError("Cloudflare Containers requires an API token")
    return NativeRuntime(configuration, credential)


def describe(configuration: CloudflareEnvironmentConfiguration) -> EnvironmentDescriptor:
    descriptor = configured_descriptor(working_directory="/workspace")
    actions = frozenset(
        action
        for action in descriptor.permissions.operations
        if ENVIRONMENT_ACTION_DISPATCH[action].family != "computer"
    )
    return descriptor.model_copy(
        update={
            "operation_families": descriptor.operation_families - {"computer"},
            "permissions": EnvironmentPermissionSet(operations=actions),
        }
    )


def identity(*, configuration: CloudflareEnvironmentConfiguration, state: EnvironmentState | None) -> str:
    value = decode_target_state("cloudflare", state, CloudflareState, fingerprint=configuration.fingerprint)
    if value is None:
        raise failure("cloudflare", "provider_state_required", Category.INVALID)
    return value.target_id


class HttpReference(CloudflareReference):
    async def close(self) -> None:
        await self.close_transport()

    def observe(self, state: CloudflareState) -> None:
        if self.target != state:
            raise failure("cloudflare", "provider_target_changed", Category.CONFLICT)


@asynccontextmanager
async def http_session(
    reference: CloudflareReference, recovery: EnvdSessionRecovery | None
) -> AsyncGenerator[EIPSession]:
    try:
        if (await reference.lookup()).status != "running":
            raise failure("cloudflare", "provider_target_unavailable", Category.UNAVAILABLE)
        assert reference.target is not None
        connection = HttpEnvdConnectionConfiguration(
            endpoint=reference.target.endpoint, request_timeout=reference.config.request_timeout_seconds
        )

        async def credential() -> HttpEnvdCredential:
            # Read-only, authenticated resolution of a private per-target guest token.
            raw = await (await reference.transport()).request(
                "GET",
                "control/credential",
                headers={"X-A13N-Environment": reference.owner, "X-A13N-Fingerprint": reference.config.fingerprint},
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("token"), str):
                raise failure("cloudflare", "envd_credential_invalid", Category.UNAVAILABLE)
            return HttpEnvdCredential(
                token=SecretStr(str(raw["token"])),
                headers={
                    "X-A13N-Bridge": SecretStr(
                        bridge_token(reference.script, reference.credential.api_key.get_secret_value())
                    ),
                    "X-A13N-Environment": SecretStr(reference.owner),
                    "X-A13N-Fingerprint": SecretStr(reference.config.fingerprint),
                },
            )

        async with managed_http_runtime(
            provider_key="cloudflare",
            connection=connection,
            endpoint_policy=reference.backend._endpoint_policy,
            load_credential=credential,
            target_id=reference.target.target_id,
        ) as carrier:
            async with carrier.open_session(
                expected_device_id=reference.owner,
                required_methods=REQUIRED_METHODS,
                working_directory="/workspace",
                session_recovery=recovery,
            ) as session:
                yield session
    finally:
        await reference.close_transport()


def http_execution(reference: CloudflareReference, recovery: EnvdSessionRecovery | None = None) -> RemoteEnvdExecution:
    reference.require_target()
    assert reference.state is not None
    return RemoteEnvdExecution(
        provider_key="cloudflare",
        environment_id=reference.environment_id,
        state=reference.state,
        session_context=http_session(reference, recovery),
        working_directory="/workspace",
        environment=reference.config.environment,
    )


def construct(
    *,
    configuration: CloudflareEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: NativeRuntime | None,
    session_recovery: EnvdSessionRecovery | None = None,
) -> RemoteEnvdExecution:
    if runtime is None:
        raise TypeError("Cloudflare Containers requires a runtime")
    return http_execution(HttpReference(configuration, environment_id, state, runtime), session_recovery)


_factory = BackendFactory(
    key="cloudflare",
    environment_model=CloudflareEnvironmentConfiguration,
    execution=construct,
    describe=describe,
    runtime_factory=runtime,
    target_identity=identity,
)
CLOUDFLARE = EnvironmentProviderDefinition(
    type="cloudflare",
    display_name="Cloudflare Containers",
    configuration_model=CloudflareConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=CloudflareEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=describe,
    target_identity=identity,
    setup_url="https://developers.cloudflare.com/containers/",
    setup_label="Cloudflare Containers",
)
