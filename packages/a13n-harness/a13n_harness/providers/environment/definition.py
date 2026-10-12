"""Immutable Environment definitions with typed account and target inputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import uuid4

from pydantic import BaseModel, JsonValue

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory, provider_error
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.definition import ProviderDefinition


class ConnectorFactory[C: BaseModel, K: BaseModel, E: BaseModel, R](Protocol):
    def __call__(
        self,
        *,
        configuration: C,
        credential: K | None,
        environment: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: R | None,
    ) -> EnvironmentConnector: ...


class TargetIdentity[E: BaseModel](Protocol):
    """Report the canonical native target selector, excluding connection metadata."""

    def __call__(self, *, configuration: E, state: EnvironmentState | None) -> str | None: ...


def _no_target_identity(*, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
    del configuration, state
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentProviderDefinition[C: BaseModel, K: BaseModel, E: BaseModel, R](ProviderDefinition[C, K]):
    """One Provider: account inputs `C`, credential `K`, execution inputs `E`, collaborator `R`.

    `configuration_model` and `credential_model` describe the account used to reach a
    backend; `environment_model` describes the desired target.
    """

    DOMAIN: ClassVar[str] = "Environment"

    environment_model: type[E]
    connector_factory: ConnectorFactory[C, K, E, R]
    describe_environment: Callable[[E], EnvironmentDescriptor]
    target_identity: TargetIdentity[E] = _no_target_identity
    backend_identity: Callable[[C], JsonValue] = lambda configuration: configuration.model_dump(mode="json")

    def validate_domain(self) -> None:
        if not isinstance(self.environment_model, type) or not issubclass(self.environment_model, BaseModel):
            raise TypeError("Environment execution inputs must be a Pydantic model")
        for factory in (self.connector_factory, self.describe_environment, self.target_identity, self.backend_identity):
            if not callable(factory):
                raise TypeError("Environment execution factories and projections must be callable")

    def validate_environment(self, value: object) -> E:
        """Validate the desired target recipe against this Provider's declared schema."""
        try:
            return self.environment_model.model_validate(value)
        except ValueError as error:
            raise provider_error(
                self.type, "provider_spec_invalid", EnvironmentProviderErrorCategory.INVALID
            ) from error

    def _account(self, configuration: object, credential: object, runtime: R | None) -> tuple[C, K | None]:
        if runtime is not None and credential is not None:
            raise ValueError("A borrowed runtime cannot be combined with credentials")
        connection = self.configuration_model.model_validate({} if configuration is None else configuration)
        if runtime is not None:
            return connection, None
        try:
            return connection, self.parse_credential(connection, credential)
        except ValueError as error:
            raise provider_error(
                self.type, "provider_credential_invalid", EnvironmentProviderErrorCategory.INVALID
            ) from error

    def execution_connector(
        self,
        environment: object,
        *,
        configuration: object = None,
        credential: object = None,
        environment_id: str | None = None,
        state: EnvironmentState | None = None,
        runtime: R | None = None,
    ) -> EnvironmentConnector:
        """Validate inert inputs for one fixed target; acquire nothing until open()."""
        desired = self.validate_environment(environment)
        account, secret = self._account(configuration, credential, runtime)
        self.target_identity(configuration=desired, state=state)
        return self.connector_factory(
            configuration=account,
            credential=secret,
            environment=desired,
            environment_id=environment_id or "env-" + uuid4().hex,
            state=state,
            runtime=runtime,
        )
