"""Host-approved Project-root adapters for Environment Provider profiles."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from a13n_envd_client.eip.v1 import GrantAccess, RestrictedSandbox
from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.envd_policy import EnvdNetworkConfiguration
from a13n_environment.execution import EnvironmentConnector
from a13n_environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
)
from a13n_environment.local_envd.provider import LOCAL_ENVD
from a13n_environment.models import EnvironmentState
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_harness_ui.environment_profiles import (
    FULL_CONTROL_PROFILE,
    SANDBOX_PROFILE,
)
from a13n_harness_ui.errors import CompositionError

if TYPE_CHECKING:
    from a13n_harness_ui.composition.models import ResolvedEnvironmentProfile

NATIVE_PROVIDER_KEY = FULL_CONTROL_PROFILE.provider_key
LOCAL_ENVD_PROVIDER_KEY = SANDBOX_PROFILE.provider_key
NATIVE_ADAPTER_KEY = FULL_CONTROL_PROFILE.adapter_key
LOCAL_ENVD_ADAPTER_KEY = SANDBOX_PROFILE.adapter_key


class ValidatedAdapterConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class _EmptyAdapterConfiguration(ValidatedAdapterConfiguration):
    pass


class EnvironmentProjectAdapter(ABC):
    """Trusted Host mapping from one profile and Project root to one Environment."""

    key: ClassVar[str]
    provider_key: ClassVar[str]
    preserves_host_paths: ClassVar[bool] = False

    @abstractmethod
    def validate_profile(
        self,
        *,
        provider_configuration: Mapping[str, JsonValue],
        adapter_configuration: Mapping[str, JsonValue],
        provider: EnvironmentProviderDefinition,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        """Validate folder-independent behavior and return normalized JSON."""

    @abstractmethod
    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        root: Path,
        state: EnvironmentState | None,
        provider: EnvironmentProviderDefinition,
        runtime: object | None,
    ) -> EnvironmentConnector:
        """Prepare the Host-selected target and return its inert execution connector.

        Management failures must preserve observed state for Host publication.
        """


class NativeProjectAdapter(EnvironmentProjectAdapter):
    key = NATIVE_ADAPTER_KEY
    provider_key = NATIVE_PROVIDER_KEY
    preserves_host_paths = True

    def validate_profile(
        self,
        *,
        provider_configuration: Mapping[str, JsonValue],
        adapter_configuration: Mapping[str, JsonValue],
        provider: EnvironmentProviderDefinition,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        _require_provider(provider, DIRECT_LOCAL)
        _require_empty(provider_configuration, adapter_configuration)
        return {}, {}

    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        root: Path,
        state: EnvironmentState | None,
        provider: EnvironmentProviderDefinition,
        runtime: object | None,
    ) -> EnvironmentConnector:
        _require_provider(provider, DIRECT_LOCAL)
        shell = _host_shell()
        value: dict[str, JsonValue] = {
            "root": {"path": str(root)},
            "shell_profiles": (
                []
                if shell is None
                else [
                    {
                        "profile_id": "default",
                        "executable": str(shell),
                        "dialect": "powershell" if sys.platform == "win32" else "posix",
                        "allow_login": sys.platform != "win32",
                        "fixed_arguments": ["-NoLogo", "-NoProfile", "-NonInteractive"]
                        if sys.platform == "win32"
                        else [],
                    }
                ]
            ),
            "inherit_environment": True,
            "allowed_environment_keys": None,
        }
        configuration = provider.validate_environment(value)
        return provider.execution_connector(
            environment=configuration,
            environment_id=_environment_id(self.key, root),
            state=state,
            runtime=runtime,
        )


class LocalEnvdProfileConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    launch: LocalEnvdLaunchConfiguration = Field(
        default_factory=lambda: LocalEnvdLaunchConfiguration(
            sandbox=RestrictedSandbox(mode="restricted", grants=()),
            egress=EnvdNetworkConfiguration(mode="deny"),
        )
    )
    session: LocalEnvdEnvironmentConfiguration = Field(default_factory=LocalEnvdEnvironmentConfiguration)

    @model_validator(mode="after")
    def _consistent_policy(self) -> LocalEnvdProfileConfiguration:
        if (self.launch.egress.mode == "controlled") != (self.session.egress is not None):
            raise ValueError("controlled egress requires an explicit Session policy; other modes reject it")
        if self.session.working_directory is not None:
            raise ValueError("The Project adapter selects the Session working directory")
        return self


class LocalEnvdProjectConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_access: GrantAccess = GrantAccess.READ_WRITE


class LocalEnvdProjectAdapter(EnvironmentProjectAdapter):
    key = LOCAL_ENVD_ADAPTER_KEY
    provider_key = LOCAL_ENVD_PROVIDER_KEY
    preserves_host_paths = True

    def validate_profile(
        self,
        *,
        provider_configuration: Mapping[str, JsonValue],
        adapter_configuration: Mapping[str, JsonValue],
        provider: EnvironmentProviderDefinition,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        _require_provider(provider, LOCAL_ENVD)
        return (
            LocalEnvdProfileConfiguration.model_validate(dict(provider_configuration)).model_dump(mode="json"),
            LocalEnvdProjectConfiguration.model_validate(dict(adapter_configuration)).model_dump(mode="json"),
        )

    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        root: Path,
        state: EnvironmentState | None,
        provider: EnvironmentProviderDefinition,
        runtime: object | None,
    ) -> EnvironmentConnector:
        _require_provider(provider, LOCAL_ENVD)
        selected = LocalEnvdProfileConfiguration.model_validate(profile.provider_configuration)
        configuration = selected.session.model_copy(update={"working_directory": root.as_posix()})
        return provider.execution_connector(
            environment=configuration,
            environment_id=_environment_id(self.key, root),
            state=state,
            runtime=runtime,
        )


def _require_provider(provider: EnvironmentProviderDefinition, expected_type: EnvironmentProviderDefinition) -> None:
    if provider.type != expected_type.type:
        raise CompositionError(
            "The Environment profile is incompatible with its Host adapter.",
            code="environment_adapter_incompatible",
        )


def _require_empty(
    provider_configuration: Mapping[str, JsonValue],
    adapter_configuration: Mapping[str, JsonValue],
) -> None:
    if provider_configuration or adapter_configuration:
        raise CompositionError(
            "This Environment adapter does not accept profile configuration.",
            code="environment_profile_configuration_invalid",
        )


def _host_shell() -> Path | None:
    if sys.platform == "win32":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        return (
            Path(powershell)
            if powershell
            else Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
    return Path(os.environ.get("SHELL", "/bin/sh"))


def _environment_id(prefix: str, root: Path) -> str:
    return f"{prefix}-{hashlib.sha256(os.fsencode(root)).hexdigest()[:16]}"


__all__ = [
    "LOCAL_ENVD_ADAPTER_KEY",
    "LOCAL_ENVD_PROVIDER_KEY",
    "NATIVE_ADAPTER_KEY",
    "NATIVE_PROVIDER_KEY",
    "EnvironmentProjectAdapter",
    "LocalEnvdProjectAdapter",
    "NativeProjectAdapter",
    "ValidatedAdapterConfiguration",
]
