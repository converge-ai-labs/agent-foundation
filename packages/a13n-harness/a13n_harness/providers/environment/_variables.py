"""Ordinary environment defaults shared by managed execution adapters."""

import re
from collections.abc import Mapping
from typing import Annotated

from pydantic import AfterValidator, Field

from a13n_harness.environment.commands import CommandEnvironment, CommandRequest


def validate_variables(values: dict[str, str]) -> dict[str, str]:
    for name, value in values.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or "\x00" in value:
            raise ValueError("Environment variables require portable names and values without NUL")
        upper = name.upper()
        if upper.startswith(("A13N_ENVD_", "EIP_", "A13N_DESKTOP_", "OTEL_")) or upper in {
            "A13N_DESKTOP",
            "DISPLAY",
            "XAUTHORITY",
        }:
            raise ValueError("Environment defaults cannot override platform-owned variables")
    return dict(sorted(values.items()))


type EnvironmentVariables = Annotated[
    dict[str, str],
    AfterValidator(validate_variables),
    Field(
        description="Default variables for Agent commands. Ordinary readable configuration, not secret storage. Command variables override these defaults."
    ),
]


def with_defaults(request: CommandRequest, defaults: Mapping[str, str], *, windows: bool = False) -> CommandRequest:
    if not defaults:
        return request

    def key(name: str) -> str:
        return name.upper() if windows else name

    unset = {key(name) for name in request.environment.unset}
    values = {key(name): value for name, value in defaults.items() if key(name) not in unset}
    values.update({key(name): value for name, value in request.environment.set.items()})
    return request.model_copy(update={"environment": CommandEnvironment(set=values, unset=request.environment.unset)})
