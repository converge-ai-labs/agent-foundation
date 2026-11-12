"""Nonsecret synchronization evidence and short-lived resolved credential snapshots."""

from dataclasses import dataclass, field
from datetime import datetime

from a13n_envd_client.eip.v1 import EgressSecret, EgressStatus, EgressUpdateParams
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from a13n_service.resources.environment_instances.session_configuration import SessionConfiguration


class SyncState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    device_generation: int = Field(ge=1, le=18446744073709551615)
    revision: int | None = Field(default=None, ge=1, le=18446744073709551615)
    failures: int = Field(default=0, ge=0)
    retry_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class Credential:
    id: str
    version: int
    value: SecretStr | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class Snapshot:
    configuration: SessionConfiguration
    credentials: dict[str, Credential] = field(repr=False)

    @property
    def revoked(self) -> bool:
        return any(item.value is None for item in self.credentials.values())

    def native_configuration(self):
        return self.configuration.resolve({key: value.value for key, value in self.credentials.items()})

    def update(self, status: EgressStatus) -> EgressUpdateParams:
        sets, removals = [], []
        for binding in self.configuration.credential_references:
            value = self.credentials[binding.credential_id].value
            if value is None:
                removals.append(binding.env)
            else:
                sets.append(
                    EgressSecret(env=binding.env, value=value.get_secret_value(), inject_hosts=binding.inject_hosts)
                )
        assert self.configuration.egress is not None
        return EgressUpdateParams(
            expected_revision=status.revision,
            destinations=self.configuration.egress.destinations,
            set_secrets=tuple(sets),
            remove_secrets=tuple(removals),
        )
