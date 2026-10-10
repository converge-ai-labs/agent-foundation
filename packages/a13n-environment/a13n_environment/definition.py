"""Immutable Environment definitions with typed account and target inputs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Protocol
from uuid import uuid4

from pydantic import BaseModel, JsonValue

from ._metadata import validate_definition
from .credential_policy import CredentialMode, CredentialPolicy
from .errors import EnvironmentProviderErrorCategory, provider_error
from .execution import EnvironmentConnector
from .management import EnvironmentProvider
from .models import EnvironmentDescriptor, EnvironmentState

NO_CREDENTIAL = CredentialPolicy(mode=CredentialMode.forbidden)


class ProviderFactory[C: BaseModel, K: BaseModel, E: BaseModel, R](Protocol):
    def __call__(
        self, *, configuration: C, credential: K | None, runtime: R | None
    ) -> Awaitable[EnvironmentProvider[E]]: ...


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
class EnvironmentProviderDefinition[C: BaseModel, K: BaseModel, E: BaseModel, R]:
    """One Provider: account inputs `C`, credential `K`, target recipe `E`, collaborator `R`.

    `configuration_model` and `credential_model` describe the account used to reach a
    backend; `environment_model` describes the desired target.
    """

    DOMAIN: ClassVar[str] = "Environment"

    type: str
    display_name: str
    configuration_model: type[C]
    credential_model: type[K] | None = None
    credential_policy: CredentialPolicy = field(default_factory=CredentialPolicy)
    setup_url: str | None = None
    setup_label: str | None = None

    environment_model: type[E]
    connector_factory: ConnectorFactory[C, K, E, R]
    provider_factory: ProviderFactory[C, K, E, R] | None = None
    describe_environment: Callable[[E], EnvironmentDescriptor]
    target_identity: TargetIdentity[E] = _no_target_identity
    backend_identity: Callable[[C], JsonValue] = lambda configuration: configuration.model_dump(mode="json")
    supports_managed: bool = True
    supports_stop: bool = False
    supports_destroy: bool = False
    requires_keepalive: bool = False

    def __post_init__(self) -> None:
        validate_definition(
            self.DOMAIN,
            self.type,
            self.display_name,
            setup_url=self.setup_url,
            setup_label=self.setup_label,
            configuration_model=self.configuration_model,
            credential_model=self.credential_model,
        )
        if self.credential_model is None:
            if self.credential_policy not in (CredentialPolicy(), NO_CREDENTIAL):
                raise ValueError(
                    f"{self.DOMAIN} Provider {self.type!r} declares no credential model and cannot accept one"
                )
            object.__setattr__(self, "credential_policy", NO_CREDENTIAL)
        self.credential_policy.validate_configuration_model(self.configuration_model)
        self.validate_domain()

    def parse_credential(self, configuration: C | dict[str, JsonValue], credential: object) -> K | None:
        """Enforce the declared presence rule, then parse a credential this Provider accepts."""
        self.credential_policy.validate_presence(configuration, credential is not None)
        if credential is None or self.credential_model is None:
            return None
        return self.credential_model.model_validate(credential)

    def validate_domain(self) -> None:
        if not self.supports_managed and (self.supports_stop or self.supports_destroy or self.requires_keepalive):
            raise ValueError("Connect-only Environment definitions cannot declare target lifecycle capabilities")

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

    async def open_provider(
        self, *, configuration: object = None, credential: object = None, runtime: R | None = None
    ) -> EnvironmentProvider[E]:
        """Acquire management clients without selecting or changing a target."""
        if not self.supports_managed or self.provider_factory is None:
            raise provider_error(self.type, "provider_external_only", EnvironmentProviderErrorCategory.UNSUPPORTED)
        account, secret = self._account(configuration, credential, runtime)
        return await self.provider_factory(configuration=account, credential=secret, runtime=runtime)

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
