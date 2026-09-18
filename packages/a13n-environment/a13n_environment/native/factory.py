"""Typed, inert factory plumbing shared by native command providers."""

from abc import abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel

from ..management import Environment, EnvironmentProvider, ProviderRuntimeContext
from ..models import EnvironmentDescriptor, EnvironmentState
from .configuration import CommandConfiguration, NamedTargetState, TargetState
from .environment import decode_state, descriptor


@dataclass(frozen=True, slots=True)
class NativeRuntime:
    configuration: BaseModel
    credential: BaseModel
    managed: bool
    operation_id: str


class NativeProvider(EnvironmentProvider):
    provider_key: ClassVar[str]
    title: ClassVar[str]

    @property
    def key(self) -> str:
        return self.provider_key

    @property
    def display_name(self) -> str:
        return self.title

    recipe_model: ClassVar[type[CommandConfiguration]]
    state_model: ClassVar[type[TargetState]] = TargetState
    supports_destroy = True

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": self.recipe_model}

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> NativeRuntime:
        if (
            not isinstance(configuration, self.provider_configuration_model)
            or self.credential_model is None
            or not isinstance(credential, self.credential_model)
        ):
            raise TypeError(f"{self.display_name} requires its typed backend configuration and credentials")
        return NativeRuntime(configuration, credential, context.managed, context.operation_id)

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        if not isinstance(configuration, self.recipe_model):
            raise TypeError("Incorrect native provider recipe")
        return descriptor(configuration)

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
        if not isinstance(configuration, self.recipe_model):
            raise TypeError("Incorrect native provider recipe")
        value = decode_state(self.key, configuration, state, self.state_model)
        if isinstance(value, NamedTargetState):
            return value.backing_id
        return value.target_id if value else None

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, self.recipe_model) or not isinstance(runtime, NativeRuntime):
            raise TypeError("Incorrect native provider recipe or runtime")
        if (
            not isinstance(runtime.configuration, self.provider_configuration_model)
            or self.credential_model is None
            or not isinstance(runtime.credential, self.credential_model)
        ):
            raise TypeError("Incorrect native provider backend or credential")
        return self.build(configuration, environment_id, state, runtime)

    @abstractmethod
    def build(
        self,
        configuration: CommandConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ) -> Environment: ...
