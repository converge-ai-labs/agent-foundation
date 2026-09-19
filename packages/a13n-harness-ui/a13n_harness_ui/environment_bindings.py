"""Device working-directory selections shared by configuration and Run captures."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from a13n_envd_client.eip.v1.models import AbsoluteEIPPath
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentPermissionSet
from pydantic import BaseModel, ConfigDict, Field, field_validator

_HOST_ALIASES = frozenset({"workspace", "thread-files", "configuration", "builtin-skills", "user-skills"})


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
