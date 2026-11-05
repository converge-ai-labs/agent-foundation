"""Native Pod authoring with explicit platform-owned execution and storage bindings."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal, Self

from a13n_harness.providers.environment.kubernetes.configuration import (
    CREDENTIAL_DIRECTORY,
    CREDENTIAL_VOLUME,
    LABEL_PREFIX,
    DNSLabel,
    KubernetesEnvironmentConfiguration,
    Resource,
    ResourceName,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Metadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reserved(self) -> Self:
        if any(key.startswith(LABEL_PREFIX) for key in (*self.labels, *self.annotations)):
            raise ValueError(f"Metadata keys under {LABEL_PREFIX} are platform-owned")
        return self


class NativeTemplate(BaseModel):
    """Preserve native spec fields; the API server owns Kubernetes schema/admission validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    metadata: Metadata = Field(default_factory=Metadata)
    spec: dict[str, Any]


class StorageBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: DNSLabel
    mount_path: str
    claim_template: NativeTemplate | None = None
    existing_claim: ResourceName | None = None
    disposition: Literal["retain", "delete"] = "retain"
    # A directory per Environment on a borrowed claim; never recursively removed.
    shared_directory: bool = False
    read_only: bool = False

    @model_validator(mode="after")
    def source(self) -> Self:
        if (self.claim_template is None) == (self.existing_claim is None):
            raise ValueError("Select exactly one claim_template or existing_claim")
        if self.shared_directory and self.existing_claim is None:
            raise ValueError("shared_directory requires an existing claim")
        if self.name == CREDENTIAL_VOLUME:
            raise ValueError("The credential volume name is platform-owned")
        private_path(self.mount_path)
        return self


def private_path(value: str) -> None:
    path = PurePosixPath(value)
    credential = PurePosixPath(CREDENTIAL_DIRECTORY)
    if not path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise ValueError("Mount paths must be absolute normalized paths")
    if credential.is_relative_to(path) or path.is_relative_to(credential):
        raise ValueError("Mounts must not overlap the private Envd credential directory")


def native_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a native object")
    return value


def native_objects(parent: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = parent.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of native objects")
    return value


def borrowed_resources(spec: dict[str, Any]) -> list[Resource]:
    """Read only native reference shapes we consume; Kubernetes validates the full spec."""
    refs: list[Resource] = []

    def add(kind, source, field="name"):
        if source:
            refs.append(Resource(kind=kind, name=source.get(field), disposition="borrowed"))

    for volume in native_objects(spec, "volumes"):
        add("PersistentVolumeClaim", native_object(volume, "persistentVolumeClaim"), "claimName")
        add("Secret", native_object(volume, "secret"), "secretName")
        add("ConfigMap", native_object(volume, "configMap"))
        for source in native_objects(native_object(volume, "projected"), "sources"):
            add("Secret", native_object(source, "secret"))
            add("ConfigMap", native_object(source, "configMap"))
    for container in [*native_objects(spec, "containers"), *native_objects(spec, "initContainers")]:
        for entry in native_objects(container, "envFrom"):
            add("Secret", native_object(entry, "secretRef"))
            add("ConfigMap", native_object(entry, "configMapRef"))
        for entry in native_objects(container, "env"):
            source = native_object(entry, "valueFrom")
            add("Secret", native_object(source, "secretKeyRef"))
            add("ConfigMap", native_object(source, "configMapKeyRef"))
    for reference in native_objects(spec, "imagePullSecrets"):
        add("Secret", reference)
    return refs


class KubernetesRecipe(KubernetesEnvironmentConfiguration):
    pod_template: NativeTemplate
    storage: tuple[StorageBinding, ...] = ()
    network_policy: NativeTemplate | None = None

    @model_validator(mode="after")
    def bindings(self) -> Self:
        spec = self.pod_template.spec
        containers = native_objects(spec, "containers")
        names = [c.get("name") for c in containers]
        if not names or not all(isinstance(name, str) and name for name in names):
            raise ValueError("Containers require nonempty string names")
        if names.count(self.execution_container) != 1 or len(set(names)) != len(names):
            raise ValueError("execution_container must select one uniquely named regular container")
        if any(field in spec for field in ("serviceAccount", "serviceAccountName", "automountServiceAccountToken")):
            raise ValueError("The operator profile owns the sandbox ServiceAccount and token mounting")
        storage_names = [binding.name for binding in self.storage]
        if len(set(storage_names)) != len(storage_names):
            raise ValueError("Storage binding names must be unique")
        volumes = native_objects(spec, "volumes")
        if not all(isinstance(v.get("name"), str) for v in volumes):
            raise ValueError("Volumes require string names")
        if any(v.get("name") in {*storage_names, CREDENTIAL_VOLUME} for v in volumes):
            raise ValueError("Native volumes conflict with injected volume bindings")
        for container in [*containers, *native_objects(spec, "initContainers")]:
            mounts = native_objects(container, "volumeMounts")
            for mount in mounts:
                if mount.get("name") == CREDENTIAL_VOLUME:
                    raise ValueError("Only the execution container receives the private credential")
                if not isinstance(mount.get("mountPath"), str):
                    raise ValueError("volumeMounts require string mountPath values")
                private_path(mount["mountPath"])
            if container.get("name") == self.execution_container:
                if "workingDir" in container:
                    raise ValueError("Use working_directory for the execution container")
                variables = native_objects(container, "env")
                if not all(isinstance(e.get("name"), str) for e in variables):
                    raise ValueError("Container environment entries require string names")
                if any(e["name"].startswith("A13N_ENVD_") for e in variables):
                    raise ValueError("A13N_ENVD_ execution variables are platform-owned")
                paths = {m["mountPath"] for m in mounts}
                if paths.intersection(binding.mount_path for binding in self.storage):
                    raise ValueError("Native mounts conflict with injected storage bindings")
        borrowed_resources(spec)
        if self.network_policy is not None and "podSelector" in self.network_policy.spec:
            raise ValueError("The instance NetworkPolicy podSelector is platform-owned")
        return self
