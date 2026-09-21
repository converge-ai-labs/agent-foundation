"""Immutable Environment definitions with typed account and target inputs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import uuid4

from pydantic import BaseModel, JsonValue

from ..definition import ProviderDefinition
from .errors import EnvironmentProviderErrorCategory, provider_error
from .management import ClosableRuntime, Environment
from .models import EnvironmentDescriptor, EnvironmentState


class RuntimeFactory[C: BaseModel, K: BaseModel, R](Protocol):
    """Acquire the live collaborator one target needs: clients, sessions, allocations."""

    def __call__(self, *, configuration: C, credential: K | None) -> Awaitable[R]: ...


class EnvironmentConstructor[E: BaseModel, R](Protocol):
    """Build one fresh single-use adapter for the desired target configuration."""

    def __call__(
        self,
        *,
        configuration: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: R | None,
        operation_id: str,
        allow_create: bool,
    ) -> Environment: ...


class TargetIdentity[E: BaseModel](Protocol):
    """Report the canonical native target selector, excluding connection metadata."""

    def __call__(self, *, configuration: E, state: EnvironmentState | None) -> str | None: ...


def _no_target_identity(*, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
    del configuration, state
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentProviderDefinition[C: BaseModel, K: BaseModel, E: BaseModel, R](ProviderDefinition[C, K]):
    """One Provider: account inputs `C`, credential `K`, target recipe `E`, collaborator `R`.

    `configuration_model` and `credential_model` describe the account used to reach a
    backend; `environment_model` describes the desired target.
    """

    DOMAIN: ClassVar[str] = "Environment"

    environment_model: type[E]
    construct: EnvironmentConstructor[E, R]
    describe_environment: Callable[[E], EnvironmentDescriptor]
    runtime_factory: RuntimeFactory[C, K, R] | None = None
    target_identity: TargetIdentity[E] = _no_target_identity
    backend_identity: Callable[[C], JsonValue] = lambda configuration: configuration.model_dump(mode="json")
    supports_managed: bool = True
    supports_stop: bool = False
    supports_destroy: bool = False
    requires_keepalive: bool = False

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

    async def create(
        self,
        environment: object,
        *,
        configuration: object = None,
        credential: object = None,
        environment_id: str | None = None,
        state: EnvironmentState | None = None,
        operation_id: str | None = None,
        allow_create: bool = True,
        runtime: R | None = None,
    ) -> Environment:
        """Construct a single-use target; preparation and remote lifecycle remain explicit.

        A `runtime` passed in is borrowed and outlives the adapter. Without one, the
        runtime the factory acquires belongs to the adapter and closes with it.
        Account configuration and credentials belong exclusively to factory acquisition;
        target creation policy and operation identity always belong to this adapter.
        """
        if allow_create and not self.supports_managed:
            raise provider_error(self.type, "provider_external_only", EnvironmentProviderErrorCategory.UNSUPPORTED)
        if runtime is not None and (configuration is not None or credential is not None):
            raise ValueError("A borrowed runtime cannot be combined with account configuration or credentials")
        desired = self.validate_environment(environment)
        identity = environment_id or "env-" + uuid4().hex
        operation = operation_id or "op-" + uuid4().hex
        if runtime is not None:
            return self.construct(
                configuration=desired,
                environment_id=identity,
                state=state,
                runtime=runtime,
                operation_id=operation,
                allow_create=allow_create,
            )
        connection = self.configuration_model.model_validate({} if configuration is None else configuration)
        try:
            secret = self.parse_credential(connection, credential)
        except ValueError as error:
            raise provider_error(
                self.type, "provider_credential_invalid", EnvironmentProviderErrorCategory.INVALID
            ) from error
        if self.runtime_factory is None:
            return self.construct(
                configuration=desired,
                environment_id=identity,
                state=state,
                runtime=None,
                operation_id=operation,
                allow_create=allow_create,
            )
        acquired = await self.runtime_factory(configuration=connection, credential=secret)
        try:
            environment = self.construct(
                configuration=desired,
                environment_id=identity,
                state=state,
                runtime=acquired,
                operation_id=operation,
                allow_create=allow_create,
            )
        except BaseException:
            if isinstance(acquired, ClosableRuntime):
                await acquired.close()
            raise
        if isinstance(acquired, ClosableRuntime):
            environment.adopt_runtime(acquired)
        return environment
