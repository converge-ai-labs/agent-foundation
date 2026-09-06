"""Host-approved Project-root adapters for Environment Provider profiles."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    Environment,
    EnvironmentProvider,
    EnvironmentState,
    LocalEnvdEnvironmentProvider,
)
from pydantic import BaseModel, ConfigDict, JsonValue

from a13n_ui.environment_profiles import (
    FULL_CONTROL_PROFILE,
    SANDBOX_PROFILE,
)
from a13n_ui.errors import CompositionError

if TYPE_CHECKING:
    from a13n_ui.composition.models import ResolvedEnvironmentProfile

NATIVE_PROVIDER_KEY = FULL_CONTROL_PROFILE.provider_key
LOCAL_ENVD_PROVIDER_KEY = SANDBOX_PROFILE.provider_key
NATIVE_ADAPTER_KEY = FULL_CONTROL_PROFILE.adapter_key
LOCAL_ENVD_ADAPTER_KEY = SANDBOX_PROFILE.adapter_key
_NATIVE_ENVIRONMENT_KEYS = frozenset(
    {
        "CI",
        "COLORTERM",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
    }
)


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
        provider_schema_version: str,
        provider_configuration: Mapping[str, JsonValue],
        adapter_configuration: Mapping[str, JsonValue],
        provider: EnvironmentProvider,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        """Validate folder-independent behavior and return normalized JSON."""

    @abstractmethod
    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        root: Path,
        state: EnvironmentState | None,
        provider: EnvironmentProvider,
        runtime: object | None,
    ) -> Environment:
        """Create one fresh pre-entry-inert root-specific Environment."""


class NativeProjectAdapter(EnvironmentProjectAdapter):
    key = NATIVE_ADAPTER_KEY
    provider_key = NATIVE_PROVIDER_KEY
    preserves_host_paths = True

    def validate_profile(
        self,
        *,
        provider_schema_version: str,
        provider_configuration: Mapping[str, JsonValue],
        adapter_configuration: Mapping[str, JsonValue],
        provider: EnvironmentProvider,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        _require_provider(provider, DirectLocalEnvironmentProvider, provider_schema_version)
        _require_empty(provider_configuration, adapter_configuration)
        return {}, {}

    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        root: Path,
        state: EnvironmentState | None,
        provider: EnvironmentProvider,
        runtime: object | None,
    ) -> Environment:
        _require_provider(provider, DirectLocalEnvironmentProvider, profile.provider_schema_version)
        shell = _host_shell()
        environment_keys = [cast(JsonValue, key) for key in sorted(_NATIVE_ENVIRONMENT_KEYS) if key in os.environ]
        value: dict[str, JsonValue] = {
            "root": {"path": str(root), "read_only": False},
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
            "allowed_environment_keys": environment_keys,
        }
        configuration = provider.validate_configuration(
            schema_version=profile.provider_schema_version,
            value=value,
        )
        return provider.create_environment(
            configuration=configuration, environment_id=_environment_id(self.key, root), state=state, runtime=runtime
        )


class LocalEnvdProjectAdapter(EnvironmentProjectAdapter):
    key = LOCAL_ENVD_ADAPTER_KEY
    provider_key = LOCAL_ENVD_PROVIDER_KEY
    preserves_host_paths = True

    def validate_profile(
        self,
        *,
        provider_schema_version: str,
        provider_configuration: Mapping[str, JsonValue],
        adapter_configuration: Mapping[str, JsonValue],
        provider: EnvironmentProvider,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        _require_provider(provider, LocalEnvdEnvironmentProvider, provider_schema_version)
        _require_empty(provider_configuration, adapter_configuration)
        return {}, {}

    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        root: Path,
        state: EnvironmentState | None,
        provider: EnvironmentProvider,
        runtime: object | None,
    ) -> Environment:
        _require_provider(provider, LocalEnvdEnvironmentProvider, profile.provider_schema_version)
        shell = _host_shell()
        value: dict[str, JsonValue] = {
            "workspace": {"path": str(root), "read_only": False},
            "execution_network": "deny",
            "trusted_executable_roots": [] if shell is None else [str(shell.parent)],
            "shell_profiles": (
                []
                if shell is None
                else [
                    {
                        "profile_id": "default",
                        "executable": str(shell),
                        "fixed_arguments": (
                            ["-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-Command"]
                            if sys.platform == "win32"
                            else ["-c"]
                        ),
                        "allow_login": sys.platform != "win32",
                    }
                ]
            ),
        }
        configuration = provider.validate_configuration(
            schema_version=profile.provider_schema_version,
            value=value,
        )
        return provider.create_environment(
            configuration=configuration, environment_id=_environment_id(self.key, root), state=state, runtime=runtime
        )


def _require_provider(
    provider: EnvironmentProvider,
    expected_type: type[EnvironmentProvider],
    schema_version: str,
) -> None:
    if not isinstance(provider, expected_type) or schema_version not in provider.configuration_versions:
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
