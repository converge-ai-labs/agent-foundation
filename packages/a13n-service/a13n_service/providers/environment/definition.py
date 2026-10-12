"""Service management metadata composed with Harness execution definitions."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.definition import ProviderDefinition
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from pydantic import BaseModel, JsonValue

from .management import EnvironmentProvider


class ProviderFactory[C: BaseModel, K: BaseModel, E: BaseModel, R](Protocol):
    def __call__(
        self, *, configuration: C, credential: K | None, runtime: R | None
    ) -> Awaitable[EnvironmentProvider[E]]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentManagementDefinition[C: BaseModel, K: BaseModel, E: BaseModel, R](ProviderDefinition[C, K]):
    DOMAIN: ClassVar[str] = "Environment"
    execution: EnvironmentProviderDefinition[C, K, Any, R]
    environment_model: type[E]
    provider_factory: ProviderFactory[C, K, E, R]
    supports_stop: bool = False
    supports_destroy: bool = False
    requires_keepalive: bool = False

    def validate_domain(self) -> None:
        if not isinstance(self.execution, EnvironmentProviderDefinition):
            raise TypeError("Management requires a Harness execution definition")
        if not isinstance(self.environment_model, type) or not issubclass(
            self.environment_model, self.execution.environment_model
        ):
            raise TypeError("Management recipes must extend the shared execution input model")
        if not callable(self.provider_factory):
            raise TypeError("Management requires a provider factory")
        if any(type(flag) is not bool for flag in (self.supports_stop, self.supports_destroy, self.requires_keepalive)):
            raise TypeError("Management capabilities must be booleans")
        if (
            self.type != self.execution.type
            or self.configuration_model is not self.execution.configuration_model
            or self.credential_model is not self.execution.credential_model
            or self.authentication != self.execution.authentication
        ):
            raise ValueError("Management and execution definitions must share provider identity and account schemas")

    def validate_environment(self, value: object) -> E:
        try:
            return self.environment_model.model_validate(value)
        except ValueError as error:
            raise provider_error(self.type, "provider_spec_invalid", Category.INVALID) from error

    def execution_environment(self, value: object) -> BaseModel:
        recipe = self.validate_environment(value)
        fields = self.execution.environment_model.model_fields
        return self.execution.validate_environment({name: getattr(recipe, name) for name in fields})

    def describe_environment(self, value: E) -> EnvironmentDescriptor:
        return self.execution.describe_environment(self.execution_environment(value))

    def target_identity(self, *, configuration: E, state: EnvironmentState | None) -> str | None:
        return self.execution.target_identity(configuration=self.execution_environment(configuration), state=state)

    def backend_identity(self, configuration: C) -> JsonValue:
        return self.execution.backend_identity(configuration)

    async def open_provider(
        self, *, configuration: object = None, credential: object = None, runtime: R | None = None
    ) -> EnvironmentProvider[E]:
        if runtime is not None and credential is not None:
            raise ValueError("A borrowed runtime cannot be combined with credentials")
        account = self.configuration_model.model_validate({} if configuration is None else configuration)
        try:
            secret = None if runtime is not None else self.parse_credential(account, credential)
        except ValueError as error:
            raise provider_error(self.type, "provider_credential_invalid", Category.INVALID) from error
        return await self.provider_factory(configuration=account, credential=secret, runtime=runtime)


def management_definition[C: BaseModel, K: BaseModel, E: BaseModel, R](
    execution: EnvironmentProviderDefinition[C, K, Any, R],
    *,
    environment_model: type[E],
    provider_factory: ProviderFactory[C, K, E, R],
    supports_stop: bool = False,
    supports_destroy: bool = False,
    requires_keepalive: bool = False,
) -> EnvironmentManagementDefinition[C, K, E, R]:
    return EnvironmentManagementDefinition(
        type=execution.type,
        display_name=execution.display_name,
        configuration_model=execution.configuration_model,
        credential_model=execution.credential_model,
        authentication=execution.authentication,
        setup_url=execution.setup_url,
        setup_label=execution.setup_label,
        execution=execution,
        environment_model=environment_model,
        provider_factory=provider_factory,
        supports_stop=supports_stop,
        supports_destroy=supports_destroy,
        requires_keepalive=requires_keepalive,
    )
