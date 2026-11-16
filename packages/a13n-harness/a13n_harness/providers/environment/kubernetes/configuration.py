"""Kubernetes account selection, trusted Host authentication and exact native identity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, field_validator, model_validator

from a13n_harness.environment.envd_settings import EnvdSettings
from a13n_harness.environment.models import EnvironmentState, EnvironmentTargetState, decode_target_state
from a13n_harness.environment.variables import EnvironmentVariables
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.configuration import EnvironmentProviderConfiguration

KEY = "kubernetes"
LABEL_PREFIX = "environments.a13n.dev/"
CREDENTIAL_VOLUME = "a13n-envd-credential"
CREDENTIAL_DIRECTORY = "/var/lib/a13n-envd/credential"
CREDENTIAL_FILE = CREDENTIAL_DIRECTORY + "/token"
DNSLabel = Annotated[str, Field(pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$", max_length=63)]
ResourceName = Annotated[str, Field(pattern=r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$", max_length=253)]


class ClusterProfile(BaseModel):
    """Trusted process configuration; never accepted as a tenant Provider credential.

    Kubeconfig may invoke operator-installed authentication plugins. Both the path
    and context are explicit; in-cluster authentication uses the projected token.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    authentication: Literal["in_cluster", "kubeconfig"] = "in_cluster"
    kubeconfig: Path | None = None
    context: str | None = None
    namespaces: tuple[DNSLabel, ...] = Field(min_length=1)
    ownership_id: DNSLabel
    sandbox_service_account: DNSLabel = "default"
    allow_port_forward: bool = False
    allow_plaintext_envd: bool = False
    kubectl: str = "kubectl"
    request_timeout_seconds: float = Field(default=15, gt=0, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def authentication_source(self) -> Self:
        if self.authentication == "kubeconfig":
            if self.kubeconfig is None or not self.kubeconfig.is_absolute() or not self.context:
                raise ValueError("Kubeconfig authentication requires an absolute trusted path and an explicit context")
        elif self.kubeconfig is not None or self.context is not None:
            raise ValueError("In-cluster authentication does not use a kubeconfig or context")
        return self


class KubernetesConnectionConfiguration(EnvironmentProviderConfiguration):
    cluster: str = Field(min_length=1, max_length=128, description="Operator-configured cluster profile")
    namespace: DNSLabel
    _cluster: ClusterProfile | None = PrivateAttr(default=None)
    _bootstrap: SecretStr | None = PrivateAttr(default=None)
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)


class KubernetesEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    execution_container: DNSLabel
    connection: Literal["direct", "port_forward"] = "direct"
    working_directory: str = "/workspace"
    envd_port: int = Field(default=8888, ge=1, le=65535)
    request_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    environment: EnvironmentVariables = Field(default_factory=dict)
    envd: EnvdSettings = Field(default_factory=EnvdSettings, exclude_if=lambda value: not value.overrides())

    @field_validator("working_directory")
    @classmethod
    def directory(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or "\x00" in value:
            raise ValueError("working_directory must be an absolute normalized path")
        return value


def fingerprint(configuration: KubernetesEnvironmentConfiguration) -> str:
    # Management-only native manifests stay frozen in the Host's recipe. Execution
    # validates its shared binding without importing the allocation recipe.
    # Preserve legacy scalar encoding, including integer-valued float defaults.
    values = {
        key: getattr(configuration, key) for key in KubernetesEnvironmentConfiguration.model_fields if key != "envd"
    }
    if overrides := configuration.envd.overrides():
        values["envd"] = overrides
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Resource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["Pod", "Secret", "ConfigMap", "PersistentVolumeClaim", "NetworkPolicy"]
    name: ResourceName
    uid: str | None = Field(default=None, min_length=1)
    disposition: Literal["delete", "retain", "borrowed"] = "delete"
    # Shared PVC directories stay discoverable after the live recipe is cleared.
    sub_paths: tuple[str, ...] = ()


class KubernetesState(EnvironmentTargetState):
    cluster: str
    namespace: DNSLabel
    ownership_id: DNSLabel
    pod_name: ResourceName
    pod_uid: str | None = None
    resources: tuple[Resource, ...] = ()
    # Frozen operator identity and sandbox ServiceAccount; operational settings remain live.
    profile_fingerprint: str
    recipe_fingerprint: str
    stage: str = "resources"

    def envelope(self) -> EnvironmentState:
        return EnvironmentState(provider_key=KEY, state_version="1", state=self.model_dump(mode="json"))


def decode(configuration: KubernetesEnvironmentConfiguration, state: EnvironmentState | None) -> KubernetesState | None:
    return decode_target_state(KEY, state, KubernetesState, fingerprint=fingerprint(configuration))
