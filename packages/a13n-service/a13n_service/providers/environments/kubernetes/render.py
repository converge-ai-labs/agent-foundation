"""Deterministic native resources; this module performs no external I/O."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.providers.environment.kubernetes.configuration import (
    CREDENTIAL_FILE,
    CREDENTIAL_VOLUME,
    LABEL_PREFIX,
    ClusterProfile,
    KubernetesConnectionConfiguration,
    KubernetesState,
    Resource,
    fingerprint,
)
from a13n_harness.providers.environment.kubernetes.runtime import labels, profile_fingerprint

from a13n_service.providers.environment.envd import envd_environment
from a13n_service.providers.environment.recipes.kubernetes import KubernetesRecipe, Metadata, borrowed_resources


def recipe_fingerprint(recipe: KubernetesRecipe) -> str:
    return hashlib.sha256(
        json.dumps(recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def initial(
    recipe: KubernetesRecipe, connection: KubernetesConnectionConfiguration, environment_id: str
) -> KubernetesState:
    assert connection._cluster is not None
    name = "a13n-" + hashlib.sha256(environment_id.encode()).hexdigest()[:32]
    state = KubernetesState(
        environment_id=environment_id,
        configuration_fingerprint=fingerprint(recipe),
        cluster=connection.cluster,
        namespace=connection.namespace,
        ownership_id=connection._cluster.ownership_id,
        pod_name=name,
        profile_fingerprint=profile_fingerprint(connection._cluster),
        recipe_fingerprint=recipe_fingerprint(recipe),
    )
    resources = [Resource(kind="Secret", name=name + "-envd")]
    for binding in recipe.storage:
        resources.append(
            Resource(
                kind="PersistentVolumeClaim",
                name=binding.existing_claim or name + "-" + binding.name,
                disposition="borrowed" if binding.existing_claim else binding.disposition,
                sub_paths=(name,) if binding.shared_directory else (),
            )
        )
    resources.extend(borrowed_resources(recipe.pod_template.spec))
    if recipe.network_policy is not None:
        resources.append(Resource(kind="NetworkPolicy", name=name))
    resources.append(Resource(kind="Pod", name=name))
    # Native and injected references can name the same borrowed resource.
    unique: dict[tuple[str, str], Resource] = {}
    for reference in resources:
        key = (reference.kind, reference.name)
        if key in unique and (reference.disposition != "borrowed" or unique[key].disposition != "borrowed"):
            raise provider_error("kubernetes", "kubernetes_resource_collision", Category.INVALID)
        previous = unique.get(key)
        unique[key] = reference.model_copy(
            update={"sub_paths": tuple(sorted(set(reference.sub_paths) | set(previous.sub_paths if previous else ())))}
        )
    return state.model_copy(update={"resources": tuple(unique.values())})


def metadata(state: KubernetesState, name: str, native: Metadata | None = None) -> dict[str, Any]:
    native = native or Metadata()
    return {
        "name": name,
        "namespace": state.namespace,
        "labels": {**native.labels, **labels(state)},
        "annotations": {**native.annotations, LABEL_PREFIX + "recipe": state.recipe_fingerprint},
    }


def render(
    recipe: KubernetesRecipe, profile: ClusterProfile, state: KubernetesState, reference: Resource
) -> dict[str, Any]:
    """Render public configuration. The Secret token is supplied privately immediately before dispatch."""
    kind = reference.kind
    body: dict[str, Any] = {"apiVersion": "v1", "kind": kind, "metadata": metadata(state, reference.name)}
    if kind == "Secret":
        body.update(type="Opaque", immutable=True)
    elif kind == "PersistentVolumeClaim":
        binding = next(b for b in recipe.storage if state.pod_name + "-" + b.name == reference.name)
        assert binding.claim_template is not None
        body.update(
            metadata=metadata(state, reference.name, binding.claim_template.metadata),
            spec=deepcopy(binding.claim_template.spec),
        )
    elif kind == "NetworkPolicy":
        assert recipe.network_policy is not None
        body.update(
            apiVersion="networking.k8s.io/v1",
            metadata=metadata(state, reference.name, recipe.network_policy.metadata),
            spec={**deepcopy(recipe.network_policy.spec), "podSelector": {"matchLabels": labels(state)}},
        )
    elif kind == "Pod":
        spec = deepcopy(recipe.pod_template.spec)
        spec.update(serviceAccountName=profile.sandbox_service_account, automountServiceAccountToken=False)
        spec.setdefault("restartPolicy", "Never")
        container = next(c for c in spec["containers"] if c["name"] == recipe.execution_container)
        container["workingDir"] = recipe.working_directory
        container.setdefault("env", []).extend(
            {"name": key, "value": value}
            for key, value in envd_environment(
                {
                    "A13N_ENVD_DEVICE_ID": state.environment_id,
                    "A13N_ENVD_TRANSPORT": "http",
                    "A13N_ENVD_HTTP_BIND": f"0.0.0.0:{recipe.envd_port}",
                    "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
                    "A13N_ENVD_HTTP_CREDENTIAL_FILE": CREDENTIAL_FILE,
                },
                recipe.envd,
            ).items()
        )
        spec.setdefault("volumes", []).append(
            {"name": CREDENTIAL_VOLUME, "secret": {"secretName": state.pod_name + "-envd", "defaultMode": 0o400}}
        )
        # An immutable Secret subPath is a regular file, matching Envd's bootstrap contract.
        container.setdefault("volumeMounts", []).append(
            {"name": CREDENTIAL_VOLUME, "mountPath": CREDENTIAL_FILE, "subPath": "token", "readOnly": True}
        )
        for binding in recipe.storage:
            spec["volumes"].append(
                {
                    "name": binding.name,
                    "persistentVolumeClaim": {
                        "claimName": binding.existing_claim or state.pod_name + "-" + binding.name,
                        "readOnly": binding.read_only,
                    },
                }
            )
            container["volumeMounts"].append(
                {
                    "name": binding.name,
                    "mountPath": binding.mount_path,
                    "readOnly": binding.read_only,
                    **({"subPath": state.pod_name} if binding.shared_directory else {}),
                }
            )
        body.update(metadata=metadata(state, reference.name, recipe.pod_template.metadata), spec=spec)
    else:
        raise ValueError("Borrowed resources are never rendered for creation")
    return body
