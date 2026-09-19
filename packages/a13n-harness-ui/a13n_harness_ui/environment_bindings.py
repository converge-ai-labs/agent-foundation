"""Device working-directory selections shared by configuration and Run captures."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Self

from a13n_envd_client.eip.v1.models import AbsoluteEIPPath
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentPermissionSet
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

_HOST_ALIASES = frozenset({"workspace", "thread-files", "configuration", "builtin-skills", "user-skills"})


def validate_local_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    """Validate saved path references without consulting the filesystem."""
    if len(roots) != len(set(roots)):
        raise ValueError("Local roots must be unique and ordered")
    if any(not root or len(root) > 4096 or "\x00" in root or not Path(root).is_absolute() for root in roots):
        raise ValueError("Local roots must be absolute, NUL-free paths of at most 4096 characters")
    return roots


type LocalRoots = Annotated[tuple[str, ...], Field(max_length=64), AfterValidator(validate_local_roots)]


class EnvironmentBindingSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str = Field(min_length=3, max_length=128, pattern=r"^device-[a-z0-9]+(?:-[a-z0-9]+)*$")
    working_directory: AbsoluteEIPPath
    alias: str = Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
    permission_ceiling: EnvironmentPermissionSet = Field(
        default_factory=lambda: EnvironmentPermissionSet(operations=frozenset(EnvironmentAction))
    )

    @field_validator("permission_ceiling", mode="before")
    @classmethod
    def _permission_ceiling(cls, value: object) -> object:
        # Parent configuration normalizers enter Python mode before nested
        # validation; retain the native JSON semantics of the action frozenset.
        if isinstance(value, dict):
            return EnvironmentPermissionSet.model_validate_json(json.dumps(value), strict=True)
        return value

    @field_validator("alias")
    @classmethod
    def _not_host_alias(cls, value: str) -> str:
        if value in _HOST_ALIASES or re.fullmatch(r"(?:workspace|content-plugin)-[0-9]+", value):
            raise ValueError("Environment alias is reserved for Host mounts")
        return value


class EnvironmentSelectionPatch(BaseModel):
    """Run-only choices; omitted fields retain the Thread selection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    local_roots: LocalRoots | None = None
    environment_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    environment_bindings: tuple[EnvironmentBindingSelection, ...] | None = Field(default=None, max_length=64)
    default_environment: str | None = Field(default=None, min_length=1, max_length=63)

    @model_validator(mode="after")
    def _non_null_selections(self) -> Self:
        for name, value in self.model_dump(exclude_unset=True).items():
            if name != "default_environment" and value is None:
                raise ValueError(f"{name} cannot be null when supplied")
        validate_binding_aliases(self.environment_bindings or ())
        return self


def validate_binding_aliases(bindings: Sequence[EnvironmentBindingSelection]) -> None:
    aliases = [binding.alias for binding in bindings]
    if len(aliases) != len(set(aliases)):
        raise ValueError("Environment binding aliases must be unique")


def validate_environment_selection(
    bindings: Sequence[EnvironmentBindingSelection], default: str | None, *, local_root_count: int
) -> None:
    """Validate the selected working mounts without consulting either filesystem."""
    validate_binding_aliases(bindings)
    if bindings and default is None:
        raise ValueError("Added Environment bindings require an explicit default_environment")
    aliases = {binding.alias for binding in bindings}
    aliases.add("thread-files")
    if local_root_count:
        aliases.add("workspace")
        aliases.update(f"workspace-{index}" for index in range(2, local_root_count + 1))
    if default is not None and default not in aliases:
        raise ValueError("default_environment must select an available working mount alias")
