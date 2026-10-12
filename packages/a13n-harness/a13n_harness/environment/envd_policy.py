"""Nonsecret Envd selection and private, preparation-time credential resolution."""

from __future__ import annotations

import os
from typing import Annotated, Literal, Protocol

from a13n_envd_client import EIPSessionStateError
from a13n_envd_client.eip.v1 import (
    EgressDestinations,
    EgressMode,
    EgressPolicy,
    EgressSecret,
    ExecutionBoundary,
    ExecutionIdentity,
    SandboxPolicy,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class EnvironmentCredentialSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["environment"]
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvdSecretReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)
    source: EnvironmentCredentialSource
    inject_hosts: tuple[str, ...] = Field(min_length=1, max_length=256)


class EnvdEgressConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destinations: EgressDestinations
    secrets: tuple[EnvdSecretReference, ...] = Field(default=(), max_length=64)

    @property
    def source_environment_names(self) -> frozenset[str]:
        return frozenset(binding.source.name for binding in self.secrets)


class EnvdBoundaryRequirement(BaseModel):
    """A selected remote boundary, not authority to change that Device."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox: SandboxPolicy
    egress: EgressMode
    identity: ExecutionIdentity | None = None
    privilege_gain_blocked: bool | None = None

    def check(self, actual: ExecutionBoundary) -> None:
        if (
            actual.sandbox != self.sandbox
            or actual.egress != self.egress
            or (self.identity is not None and actual.identity != self.identity)
            or (
                self.privilege_gain_blocked is not None and actual.privilege_gain_blocked != self.privilege_gain_blocked
            )
        ):
            raise EIPSessionStateError("Device execution boundary does not match the selected requirement")


class EnvdCredentialResolver(Protocol):
    async def resolve(self, source: EnvironmentCredentialSource) -> SecretStr: ...


class EnvironmentEnvdCredentialResolver:
    async def resolve(self, source: EnvironmentCredentialSource) -> SecretStr:
        value = os.environ.get(source.name)
        if not value:
            raise ValueError(f"Required Envd credential source is unavailable: {source.name}")
        return SecretStr(value)


async def resolve_egress(
    configuration: EnvdEgressConfiguration | None,
    resolver: EnvdCredentialResolver,
) -> EgressPolicy | None:
    if configuration is None:
        return None
    secrets: list[EgressSecret] = []
    for binding in configuration.secrets:
        value = await resolver.resolve(binding.source)
        if not 1 <= len(value.get_secret_value()) <= 8192:
            raise ValueError("Envd credential value must contain 1 to 8192 characters")
        secrets.append(EgressSecret(env=binding.env, value=value.get_secret_value(), inject_hosts=binding.inject_hosts))
    return EgressPolicy(destinations=configuration.destinations, secrets=tuple(secrets))


class EnvdExecutionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: Annotated[int, Field(ge=0, lt=4294967295)] | None = None
    gid: Annotated[int, Field(ge=0, lt=4294967295)] | None = None
    allow_sudo: bool = True


class EnvdNetworkConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["inherit", "deny", "controlled"]
