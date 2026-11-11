"""Service-owned Envd lifecycle policy, independent of execution compatibility."""

from typing import Literal, Self

from a13n_harness.providers.environment.execution_mode import ExecutionMode, execution_mode
from packaging.version import Version
from pydantic import BaseModel, Field, model_validator

from .envd_release import VERSION


class EnvdRecipe(BaseModel):
    envd_management: Literal["managed", "preinstalled"] = Field(
        default="preinstalled",
        title="Envd binary management",
        description="Managed: Service prepares and upgrades its pinned Envd release using the standard launcher. Preinstalled: the operator supplies Envd and its startup; Service checks readiness and never installs or upgrades Envd or replaces the authored startup. Automatic preparation assets require managed. Managed custom runtimes require standard root-owned Envd paths, the native supervisor and GNU/Linux installer prerequisites.",
    )

    @model_validator(mode="after")
    def envd_execution(self) -> Self:
        if self.envd_management == "managed" and execution_mode(self) == ExecutionMode.NATIVE:
            raise ValueError("Managed Envd requires envd_http or envd_ws execution")
        return self


def managed(recipe: BaseModel) -> bool:
    return isinstance(recipe, EnvdRecipe) and recipe.envd_management == "managed"


def update_required(applied: str | None, selected: str | None = None) -> bool:
    """Older Service replicas must not automatically downgrade a prepared instance."""
    return applied is None or Version(applied) < Version(selected or VERSION)


def selected_version(applied: str | None) -> str:
    return applied if applied is not None and Version(applied) > Version(VERSION) else VERSION
