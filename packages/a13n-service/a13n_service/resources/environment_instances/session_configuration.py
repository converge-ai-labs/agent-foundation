"""Tenant Session selection: resource references instead of Host environment secrets."""

from collections.abc import Mapping

from a13n_envd_client.eip.v1 import (
    EgressDestinations,
    EgressMode,
    EgressPolicy,
    EgressSecret,
    SandboxPolicy,
    SessionIsolation,
    SessionLifecyclePolicy,
)
from a13n_envd_client.eip.v1 import SessionConfiguration as NativeSessionConfiguration
from a13n_envd_client.eip.v1.models import AbsoluteEIPPath
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from a13n_service.resources.egress_credentials.schemas import SecretReference


class EgressConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destinations: EgressDestinations
    secrets: tuple[SecretReference, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def valid_bindings(self) -> "EgressConfiguration":
        if len({binding.env for binding in self.secrets}) != len(self.secrets):
            raise ValueError("Credential variable names must be unique within a Session")
        if self.destinations.mode == "allowlist":
            hosts = {host.removesuffix(".").lower() for host in self.destinations.hosts}
            if any(not set(binding.inject_hosts) <= hosts for binding in self.secrets):
                raise ValueError("Credential injection hosts must be allowed destinations")
        return self


class SessionConfiguration(BaseModel):
    """Tenant-authored Session policy; Service credentials are not process-environment references."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    working_directory: AbsoluteEIPPath | None = None
    sandbox: SandboxPolicy | None = None
    egress_mode: EgressMode | None = None
    egress: EgressConfiguration | None = None
    lifecycle: SessionLifecyclePolicy | None = None
    commands: bool | None = None
    isolation: SessionIsolation | None = None

    @model_validator(mode="after")
    def controlled_credentials(self) -> "SessionConfiguration":
        if self.credential_references and self.egress_mode != EgressMode.CONTROLLED:
            raise ValueError("Session credentials require controlled egress")
        return self

    @property
    def credential_references(self) -> tuple[SecretReference, ...]:
        return self.egress.secrets if self.egress is not None else ()

    def resolve(self, values: Mapping[str, SecretStr | None]) -> NativeSessionConfiguration:
        """Translate explicitly authorized values; never consult the Service process environment."""
        egress = None
        if self.egress is not None:
            egress = EgressPolicy(
                destinations=self.egress.destinations,
                secrets=tuple(
                    EgressSecret(
                        env=binding.env,
                        value=value.get_secret_value(),
                        inject_hosts=binding.inject_hosts,
                    )
                    for binding in self.egress.secrets
                    if (value := values[binding.credential_id]) is not None
                ),
            )
        return NativeSessionConfiguration(**self.model_dump(exclude={"egress"}), egress=egress)
