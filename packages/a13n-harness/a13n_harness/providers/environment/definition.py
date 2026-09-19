"""Immutable Environment definitions with typed account and target inputs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, JsonValue

from ..authentication import Authentication
from ..validation import validate_definition
from .errors import EnvironmentProviderErrorCategory, provider_error
from .management import EmptyProviderConfiguration, Environment
from .models import EnvironmentDescriptor, EnvironmentState


class RuntimeFactory[C: BaseModel, K: BaseModel, R](Protocol):
    """Acquire the live collaborator one target needs: clients, sessions, allocations."""

    def __call__(
        self, *, configuration: C, credential: K | None, operation_id: str, allow_create: bool
    ) -> Awaitable[R]: ...


class EnvironmentConstructor[R](Protocol):
    """Build one fresh single-use adapter; the desired configuration is version-selected."""

    def __call__(
        self, *, configuration: BaseModel, environment_id: str, state: EnvironmentState | None, runtime: R | None
    ) -> Environment: ...


class TargetIdentity(Protocol):
    """Report the canonical native target selector, excluding connection metadata."""

    def __call__(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None: ...


def _no_target_identity(*, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
    del configuration, state
    return None


@dataclass(frozen=True, slots=True)
class EnvironmentProviderDefinition[C: BaseModel, K: BaseModel, R]:
    """One Provider: account inputs `C`, credential `K`, and runtime collaborator `R`.

    `configuration_model` and `credential_model` describe the account used to reach a
    backend. `environment_models` describe the desired target, keyed by schema version.
    """

    type: str
    display_name: str
    configuration_model: type[C]
    credential_model: type[K] | None
    environment_models: Mapping[str, type[BaseModel]]
    construct: EnvironmentConstructor[R]
    describe_environment: Callable[[BaseModel], EnvironmentDescriptor]
    runtime_factory: RuntimeFactory[C, K, R] | None = None
    target_identity: TargetIdentity = _no_target_identity
    backend_identity: Callable[[C], JsonValue] = lambda configuration: configuration.model_dump(mode="json")
    authentication: Authentication = field(default_factory=Authentication)
    setup_url: str | None = None
    setup_label: str | None = None
    supports_managed: bool = True
    supports_stop: bool = False
    supports_destroy: bool = False
    requires_keepalive: bool = False

    def __post_init__(self) -> None:
        validate_definition(
            self.type,
            self.display_name,
            self.setup_url,
            self.configuration_model,
            self.credential_model or EmptyProviderConfiguration,
            domain="Environment",
            setup_label=self.setup_label,
        )
        self.authentication.validate_configuration_model(self.configuration_model)
        if not self.environment_models or not all(
            isinstance(model, type) and issubclass(model, BaseModel) for model in self.environment_models.values()
        ):
            raise ValueError("Environment definitions require typed target schemas")
        lifecycle = (self.supports_stop, self.supports_destroy, self.requires_keepalive)
        if any(type(flag) is not bool for flag in (self.supports_managed, *lifecycle)):
            raise TypeError("Environment capability declarations must be booleans")
        if not self.supports_managed and any(lifecycle):
            raise ValueError("Connect-only Environment definitions cannot declare target lifecycle capabilities")
        object.__setattr__(self, "environment_models", MappingProxyType(dict(self.environment_models)))

    @property
    def environment_versions(self) -> frozenset[str]:
        return frozenset(self.environment_models)

    def validate_environment(self, *, schema_version: str, value: object) -> BaseModel:
        """Validate the desired target recipe against one declared schema version."""
        model = self.environment_models.get(schema_version)
        try:
            if model is None:
                raise ValueError("unsupported version")
            return model.model_validate(value)
        except ValueError as error:
            raise provider_error(
                self.type,
                "provider_schema_unsupported" if model is None else "provider_spec_invalid",
                EnvironmentProviderErrorCategory.UNSUPPORTED
                if model is None
                else EnvironmentProviderErrorCategory.INVALID,
                schema_version=schema_version,
            ) from error

    async def create(
        self,
        environment: object,
        *,
        configuration: object = None,
        credential: object = None,
        schema_version: str = "1",
        environment_id: str | None = None,
        state: EnvironmentState | None = None,
        operation_id: str | None = None,
        allow_create: bool = True,
        runtime: R | None = None,
    ) -> Environment:
        """Construct a single-use target; preparation and remote lifecycle remain explicit."""
        if allow_create and not self.supports_managed:
            raise provider_error(self.type, "provider_external_only", EnvironmentProviderErrorCategory.UNSUPPORTED)
        connection = self.configuration_model.model_validate({} if configuration is None else configuration)
        try:
            self.authentication.validate_presence(connection, credential is not None)
        except ValueError as error:
            raise provider_error(
                self.type, "provider_credential_invalid", EnvironmentProviderErrorCategory.INVALID
            ) from error
        secret = (
            self.credential_model.model_validate(credential)
            if self.credential_model and credential is not None
            else None
        )
        desired = self.validate_environment(schema_version=schema_version, value=environment)
        identity = environment_id or "env-" + uuid4().hex
        if runtime is None and self.runtime_factory is not None:
            runtime = await self.runtime_factory(
                configuration=connection,
                credential=secret,
                operation_id=operation_id or "op-" + uuid4().hex,
                allow_create=allow_create,
            )
        return self.construct(configuration=desired, environment_id=identity, state=state, runtime=runtime)
