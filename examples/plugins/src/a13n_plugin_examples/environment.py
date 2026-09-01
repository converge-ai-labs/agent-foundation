"""A Host-facing Environment Provider plugin backed by Direct Local operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    Environment,
    EnvironmentProvider,
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    EnvironmentState,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

PROVIDER_KEY = "example.workspace"
_CONFIGURATION_VERSION = "1"


class WorkspaceEnvironmentConfiguration(BaseModel):
    """Credential-free schema version 1 configuration for the example Provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path
    environment_id: str = Field(min_length=1, max_length=128)
    read_only: bool = True

    @field_validator("root")
    @classmethod
    def _absolute_root(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if "\x00" in str(expanded):
            raise ValueError("root must not contain NUL")
        if not expanded.is_absolute():
            raise ValueError("root must be absolute")
        return expanded


@dataclass(frozen=True, slots=True)
class WorkspaceEnvironmentRuntime:
    """This deterministic Provider needs no credential or SDK collaborator."""


class WorkspaceEnvironmentProvider(EnvironmentProvider):
    """Construct fresh stateless adapters for one Host-selected workspace."""

    @property
    def key(self) -> str:
        return PROVIDER_KEY

    @property
    def configuration_versions(self) -> frozenset[str]:
        return frozenset({_CONFIGURATION_VERSION})

    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> BaseModel:
        if schema_version != _CONFIGURATION_VERSION:
            raise _error(
                "Unsupported example.workspace schema version.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                schema_version=schema_version,
            )
        try:
            return WorkspaceEnvironmentConfiguration.model_validate(value)
        except ValidationError as error:
            raise _error(
                "Invalid example.workspace configuration.",
                code="provider_spec_invalid",
                schema_version=schema_version,
            ) from error

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, WorkspaceEnvironmentConfiguration):
            raise TypeError("example.workspace requires WorkspaceEnvironmentConfiguration")
        if state is not None:
            raise _error(
                "example.workspace is stateless and does not accept Environment state.",
                code="provider_state_invalid",
            )
        if runtime is not None and not isinstance(runtime, WorkspaceEnvironmentRuntime):
            raise TypeError("example.workspace runtime must be WorkspaceEnvironmentRuntime or None")
        direct_configuration = DirectLocalProviderConfiguration(
            environment_id=configuration.environment_id,
            root=DirectLocalRootConfiguration(
                path=configuration.root,
                read_only=configuration.read_only,
            ),
        )
        return WorkspaceEnvironment(direct_configuration)


class WorkspaceEnvironment(DirectLocalEnvironment):
    @property
    def provider_key(self) -> str:
        return PROVIDER_KEY


def _error(
    description: str,
    *,
    code: str,
    category: EnvironmentProviderErrorCategory = EnvironmentProviderErrorCategory.INVALID,
    schema_version: str | None = None,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code=code,
        category=category,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(
            provider_key=PROVIDER_KEY,
            schema_version=schema_version,
        ),
    )


__all__ = [
    "PROVIDER_KEY",
    "WorkspaceEnvironment",
    "WorkspaceEnvironmentConfiguration",
    "WorkspaceEnvironmentProvider",
    "WorkspaceEnvironmentRuntime",
]
