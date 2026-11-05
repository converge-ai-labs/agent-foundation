"""Bounded namespace API access using the SDK's refreshable authentication."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

from anyio.to_thread import run_sync
from pydantic import BaseModel

from a13n_harness.environment.errors import (
    EnvironmentProviderErrorCategory as Category,
)
from a13n_harness.environment.errors import (
    EnvironmentProviderOutcomeCertainty as Certainty,
)
from a13n_harness.environment.errors import (
    provider_error,
)

from .configuration import (
    KEY,
    LABEL_PREFIX,
    ClusterProfile,
    KubernetesConnectionConfiguration,
    KubernetesState,
    Resource,
)

# Only these native resources belong to this provider. There is no arbitrary API runner.
RESOURCES = {
    "Pod": ("/api/v1", "pods"),
    "Secret": ("/api/v1", "secrets"),
    "ConfigMap": ("/api/v1", "configmaps"),
    "PersistentVolumeClaim": ("/api/v1", "persistentvolumeclaims"),
    "NetworkPolicy": ("/apis/networking.k8s.io/v1", "networkpolicies"),
}


def profile_fingerprint(profile: ClusterProfile) -> str:
    # Operational pacing, binaries and allowlists remain live. Native identity and
    # creation defaults must not change underneath an allocated target.
    identity = profile.model_dump_json(
        include={"authentication", "kubeconfig", "context", "ownership_id", "sandbox_service_account"}
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def labels(state: KubernetesState) -> dict[str, str]:
    return {LABEL_PREFIX + "owner": state.ownership_id, LABEL_PREFIX + "instance": state.pod_name}


def check_resource(value: dict[str, Any], reference: Resource, state: KubernetesState) -> None:
    metadata = value["metadata"]
    if reference.uid is not None and metadata["uid"] != reference.uid:
        raise provider_error(KEY, "kubernetes_uid_changed", Category.MISSING, certainty=Certainty.KNOWN)
    if reference.disposition != "borrowed" and any(
        metadata.get("labels", {}).get(k) != v for k, v in labels(state).items()
    ):
        raise provider_error(KEY, "kubernetes_ownership_conflict", Category.CONFLICT, certainty=Certainty.KNOWN)
    if (
        reference.disposition != "borrowed"
        and metadata.get("annotations", {}).get(LABEL_PREFIX + "recipe") != state.recipe_fingerprint
    ):
        raise provider_error(KEY, "kubernetes_recipe_conflict", Category.CONFLICT, certainty=Certainty.KNOWN)


class KubernetesRuntime:
    def __init__(self, connection: KubernetesConnectionConfiguration, api: Any):
        assert connection._cluster is not None
        self.connection = connection
        self.profile = connection._cluster
        self.api = api

    def validate(self, state: KubernetesState) -> None:
        if (state.cluster, state.namespace, state.ownership_id) != (
            self.connection.cluster,
            self.connection.namespace,
            self.profile.ownership_id,
        ) or state.profile_fingerprint != profile_fingerprint(self.profile):
            raise provider_error(KEY, "kubernetes_profile_changed", Category.CONFLICT)

    async def call(
        self, method: str, path: str, *, body: Any = None, query: list | None = None, text: bool = False
    ) -> Any:
        from aiohttp import ClientError
        from kubernetes_asyncio.client.exceptions import ApiException

        mutation = method != "GET"
        try:
            return await self.api.call_api(
                path,
                method,
                query_params=query or [],
                body=body,
                header_params={
                    "Accept": "*/*" if text else "application/json",
                    "Content-Type": "application/json",
                },
                response_types_map={200: "str" if text else "object", 201: "object", 202: "object"},
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
                _request_timeout=self.profile.request_timeout_seconds,
            )
        except ApiException as error:
            # These two responses are interpreted by the concrete resource operation.
            if error.status in {404, 409}:
                raise
            category = (
                Category.DENIED
                if error.status in {401, 403}
                else Category.INVALID
                if error.status in {400, 422}
                else Category.UNAVAILABLE
            )
            # A failed observation cannot settle an earlier ambiguous mutation.
            certainty = (
                Certainty.NOT_DISPATCHED
                if not mutation
                else Certainty.KNOWN
                if error.status in {400, 401, 403, 422, 429}
                else Certainty.UNKNOWN
            )
            raise provider_error(KEY, "kubernetes_api_failed", category, certainty=certainty) from None
        except (TimeoutError, ClientError, OSError):
            raise provider_error(
                KEY,
                "kubernetes_transport_failed",
                Category.UNKNOWN_OUTCOME if mutation else Category.UNAVAILABLE,
                certainty=Certainty.UNKNOWN if mutation else Certainty.NOT_DISPATCHED,
            ) from None

    def path(self, kind: str, name: str | None = None) -> str:
        prefix, plural = RESOURCES[kind]
        path = f"{prefix}/namespaces/{self.connection.namespace}/{plural}"
        return path if name is None else path + "/" + quote(name, safe="")

    async def get(self, kind: str, name: str) -> dict[str, Any] | None:
        from kubernetes_asyncio.client.exceptions import ApiException

        try:
            return await self.call("GET", self.path(kind, name))
        except ApiException as error:
            if error.status == 404:
                return None
            raise

    async def create(self, kind: str, body: dict[str, Any]) -> dict[str, Any] | None:
        from kubernetes_asyncio.client.exceptions import ApiException

        try:
            return await self.call("POST", self.path(kind), body=body, query=[("fieldValidation", "Strict")])
        except ApiException as error:
            if error.status == 409:
                # A concurrent/ambiguous create won. The next step reads and checks its identity.
                return None
            raise

    async def delete(self, reference: Resource) -> None:
        from kubernetes_asyncio.client.exceptions import ApiException

        assert reference.uid is not None
        try:
            await self.call(
                "DELETE",
                self.path(reference.kind, reference.name),
                body={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "preconditions": {"uid": reference.uid},
                    "propagationPolicy": "Foreground",
                },
            )
        except ApiException as error:
            if error.status == 404:
                return
            if error.status == 409:
                raise provider_error(
                    KEY, "kubernetes_uid_changed", Category.CONFLICT, certainty=Certainty.KNOWN
                ) from None
            raise

    async def pod(self, state: KubernetesState) -> dict[str, Any]:
        self.validate(state)
        if state.pod_uid is None:
            raise provider_error(KEY, "provider_state_required", Category.INVALID)
        pod = await self.get("Pod", state.pod_name)
        if pod is None:
            raise provider_error(KEY, "environment_not_found", Category.MISSING, certainty=Certainty.KNOWN)
        check_resource(pod, Resource(kind="Pod", name=state.pod_name, uid=state.pod_uid), state)
        return pod

    async def close(self) -> None:
        await self.api.close()


async def open_runtime(*, configuration: BaseModel, credential: BaseModel | None) -> KubernetesRuntime:
    del credential
    if not isinstance(configuration, KubernetesConnectionConfiguration):
        raise TypeError("Kubernetes requires KubernetesConnectionConfiguration")
    profile = configuration._cluster
    if profile is None or configuration.namespace not in profile.namespaces:
        raise provider_error(KEY, "kubernetes_cluster_not_allowed", Category.DENIED)
    from kubernetes_asyncio import client, config
    from kubernetes_asyncio.config.config_exception import ConfigException

    settings = client.Configuration()
    try:
        if profile.authentication == "in_cluster":
            await run_sync(lambda: config.load_incluster_config(client_configuration=settings))
        else:
            await config.load_kube_config(
                config_file=str(profile.kubeconfig),
                context=profile.context,
                client_configuration=settings,
                persist_config=False,
            )
    except (ConfigException, OSError):
        raise provider_error(KEY, "kubernetes_authentication_unavailable", Category.UNAVAILABLE) from None
    return KubernetesRuntime(configuration, client.ApiClient(settings))
