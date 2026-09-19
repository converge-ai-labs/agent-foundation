"""Authorized Device observations outside SQL and without execution ownership."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from a13n_envd_client import EIPClientError
from a13n_envd_client.eip.v1 import DeviceDescriptor, DirectoryListParams, DirectoryListResult
from a13n_environment import EnvironmentError, EnvironmentProviderError, EnvironmentState
from a13n_environment.remote_envd.configuration import HttpEnvdBackendConfiguration, HttpEnvdCredential
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_environment.remote_envd.environment import decode_state
from a13n_environment.remote_envd.http import HTTP_PROVIDER_KEY, HttpEnvdProviderRuntime
from pydantic import JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.credentials import CredentialSnapshot
from a13n_service.iam.domain import PrincipalRef
from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector

from .domain import DomainModel
from .errors import EnvironmentManagementError, invalid_environment
from .models import EnvironmentProviderRecord, EnvironmentRecord

ENVD_PROVIDER_KEYS = frozenset({HTTP_PROVIDER_KEY, WEBSOCKET_PROVIDER_KEY})


@dataclass(frozen=True, slots=True)
class DeviceTarget:
    organization_id: str
    workspace_id: str
    environment_id: str
    provider_id: str
    provider_key: str
    device_id: str
    principal: PrincipalRef
    configuration: dict[str, JsonValue] = field(repr=False)
    credential: CredentialSnapshot = field(repr=False)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.organization_id, self.environment_id, self.device_id


async def capture_device_target(
    session: AsyncSession, environment: EnvironmentRecord, *, principal: PrincipalRef
) -> DeviceTarget:
    """Capture only after the caller authorizes environment.use in this scope."""
    provider = await session.get(EnvironmentProviderRecord, environment.provider_id)
    if (
        provider is None
        or not provider.enabled
        or provider.type not in ENVD_PROVIDER_KEYS
        or environment.ownership != "external"
        or environment.status == "deleted"
        or environment.device_revoked_at is not None
        or provider.organization_id != environment.organization_id
        or provider.workspace_id not in {None, environment.workspace_id}
    ):
        raise invalid_environment("Environment is not an available envd Device")
    native = decode_state(provider.type, EnvironmentState.model_validate(environment.state))
    return DeviceTarget(
        environment.organization_id,
        environment.workspace_id,
        environment.id,
        provider.id,
        provider.type,
        native.device_id,
        principal,
        dict(provider.configuration),
        provider.credential_snapshot(),
    )


class DeviceInfo(DomainModel):
    environment_id: str
    path_style: Literal["posix", "windows"]
    default_working_directory: str
    directory_discovery: bool


# The reverse carrier owner supplies the existing bounded cross-role relay.
type DeviceRelay = Callable[
    [DeviceTarget, Literal["device.describe", "directory.list"], dict[str, JsonValue]], Awaitable[JsonValue]
]


class DeviceDiscovery:
    def __init__(self, protector: SecretProtector, *, relay: DeviceRelay | None = None) -> None:
        self._protector = protector
        self.relay = relay

    async def describe(self, target: DeviceTarget) -> DeviceDescriptor:
        try:
            if target.provider_key == HTTP_PROVIDER_KEY:
                async with self._http(target) as runtime:
                    return await runtime.describe(expected_device_id=target.device_id)
            if self.relay is None:
                raise unavailable()
            return DeviceDescriptor.model_validate(await self.relay(target, "device.describe", {}))
        except (EIPClientError, EnvironmentError, EnvironmentProviderError, SecretProtectionError) as error:
            raise unavailable() from error

    async def directories(
        self, target: DeviceTarget, *, path: str | None, offset: int = 0, limit: int = 100
    ) -> DirectoryListResult:
        descriptor = await self.describe(target)
        if not descriptor.directory_discovery:
            raise EnvironmentManagementError(
                "environment_unsupported",
                "Device directory discovery is disabled.",
                category=ErrorCategory.invalid_input,
            )
        try:
            params = DirectoryListParams(
                expected_device_id=target.device_id,
                expected_generation=descriptor.generation,
                path=descriptor.default_working_directory if path is None else path,
                offset=offset,
                limit=limit,
            )
        except ValidationError as error:
            raise invalid_environment("Directory query requires a canonical Device path and bounded page") from error
        try:
            if target.provider_key == HTTP_PROVIDER_KEY:
                async with self._http(target) as runtime:
                    return await runtime.list_directories(params)
            if self.relay is None:
                raise unavailable()
            return DirectoryListResult.model_validate(
                await self.relay(target, "directory.list", params.model_dump(mode="json"))
            )
        except (EIPClientError, EnvironmentError, EnvironmentProviderError) as error:
            raise EnvironmentManagementError(
                "environment_directory_unavailable",
                "Device directory is unavailable.",
                category=ErrorCategory.invalid_input,
            ) from error

    def _http(self, target: DeviceTarget) -> HttpEnvdProviderRuntime:
        return HttpEnvdProviderRuntime(
            HttpEnvdBackendConfiguration.model_validate(target.configuration),
            HttpEnvdCredential.model_validate_json(target.credential.decrypt(self._protector)),
        )


def unavailable() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_unavailable", "Device is unavailable.", category=ErrorCategory.unavailable
    )
