"""Provider-owned typed recipes shared with business authoring through the registry."""

from typing import Annotated, Literal

from a13n_harness.providers.environment.boxd.shared import BoxdEnvironmentConfiguration
from a13n_harness.providers.environment.direct_local.configuration import DirectLocalEnvironmentConfiguration
from a13n_harness.providers.environment.runloop.shared import RunloopEnvironmentConfiguration
from pydantic import BaseModel, ConfigDict, Field

from .daytona import DaytonaRecipe
from .docker import DockerRecipe
from .e2b import CloudRecipe
from .modal import ModalRecipe
from .sprites import SpritesRecipe
from .vercel import VercelRecipe


class _Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DockerRecipeInput(_Recipe):
    type: Literal["docker"]
    configuration: DockerRecipe


class E2BRecipeInput(_Recipe):
    type: Literal["e2b"]
    configuration: CloudRecipe


class DaytonaRecipeInput(_Recipe):
    type: Literal["daytona"]
    configuration: DaytonaRecipe


class ModalRecipeInput(_Recipe):
    type: Literal["modal"]
    configuration: ModalRecipe


class VercelRecipeInput(_Recipe):
    type: Literal["vercel"]
    configuration: VercelRecipe


class SpritesRecipeInput(_Recipe):
    type: Literal["sprites"]
    configuration: SpritesRecipe


class RunloopRecipeInput(_Recipe):
    type: Literal["runloop"]
    configuration: RunloopEnvironmentConfiguration


class BoxdRecipeInput(_Recipe):
    type: Literal["boxd"]
    configuration: BoxdEnvironmentConfiguration


class LocalRecipeInput(_Recipe):
    type: Literal["local"]
    configuration: DirectLocalEnvironmentConfiguration


TemplateRecipe = Annotated[
    DockerRecipeInput
    | E2BRecipeInput
    | DaytonaRecipeInput
    | ModalRecipeInput
    | VercelRecipeInput
    | SpritesRecipeInput
    | RunloopRecipeInput
    | BoxdRecipeInput
    | LocalRecipeInput,
    Field(discriminator="type"),
]
