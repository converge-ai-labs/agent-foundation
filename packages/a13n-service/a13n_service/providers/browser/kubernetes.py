"""Service-native Browser allocations: one StatefulSet and retained profile each."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any, Literal
from urllib.parse import urlencode

from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_harness.providers.environment.kubernetes.configuration import ClusterProfile, DNSLabel
from a13n_harness.providers.environment.kubernetes.runtime import open_cluster_client, profile_fingerprint
from kubernetes_asyncio.client import ApiClient, ApiException
from kubernetes_asyncio.config.config_exception import ConfigException
from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .definition import BrowserCheckpoint, BrowserProviderError, LiveBrowser, ViewerGrant

PREFIX = "browsers.a13n.dev/"
OWNER = PREFIX + "owner"
ALLOCATION = PREFIX + "allocation"
GENERATION = PREFIX + "generation"
TERMINAL = PREFIX + "terminal"
CREDENTIAL = "/run/a13n-browser/token"
RESOURCES = {
    "sets": ("/apis/apps/v1", "statefulsets"),
    "pods": ("/api/v1", "pods"),
    "claims": ("/api/v1", "persistentvolumeclaims"),
    "secrets": ("/api/v1", "secrets"),
}


class Cluster(ClusterProfile):
    """Trusted operator defaults; tenants cannot supply Pod or storage settings."""

    image: str = Field(min_length=1, max_length=1024)
    image_pull_policy: Literal["Always", "IfNotPresent", "Never"] = "IfNotPresent"
    service_name: DNSLabel
    storage_class: str | None = None
    storage_size: str = "2Gi"
    access_mode: Literal["ReadWriteOnce", "ReadWriteOncePod"] = "ReadWriteOncePod"
    requests: dict[str, str] = Field(default_factory=lambda: {"cpu": "250m", "memory": "512Mi"})
    limits: dict[str, str] = Field(default_factory=lambda: {"cpu": "2", "memory": "2Gi"})


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cluster: str = Field(min_length=1, max_length=128, description="Operator-configured Browser cluster profile")
    namespace: DNSLabel = Field(description="Browser workload namespace allowed by that profile")


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")


class State(BaseModel):
    allocation_id: str
    uid: str
    generation: int = 0
    profile_uid: str | None = None
    pod_uid: str | None = None
    runtime_id: str | None = None


class Kubernetes:
    def __init__(self, config: Configuration, cluster: Cluster, api: ApiClient):
        self.config, self.cluster, self.api = config, cluster, api

    async def close(self) -> None:
        await self.api.close()

    @staticmethod
    def name(allocation_id: str) -> str:
        return "a13n-browser-" + hashlib.sha256(allocation_id.encode()).hexdigest()[:32]

    def labels(self, allocation_id: str) -> dict[str, str]:
        return {OWNER: self.cluster.ownership_id, ALLOCATION: self.name(allocation_id)}

    async def call(self, kind: str, method: str, name: str = "", body: dict | None = None) -> Any:
        prefix, resource = RESOURCES[kind]
        path = f"{prefix}/namespaces/{self.config.namespace}/{resource}"
        if name:
            path += f"/{name}"
        try:
            return await self.api.call_api(
                path,
                method,
                body=body,
                header_params={
                    "Accept": "application/json",
                    "Content-Type": ("application/merge-patch+json" if method == "PATCH" else "application/json"),
                },
                response_types_map={200: "object", 201: "object", 202: "object"},
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
                _request_timeout=self.cluster.request_timeout_seconds,
            )
        except ApiException as error:
            if error.status == 404 and method == "GET":
                return None
            # Never expose Kubernetes responses: they may contain Secret data.
            raise BrowserProviderError(
                f"kubernetes_{kind}_{error.status}",
                certainty=("rejected" if error.status in {400, 401, 403, 422} else "unknown"),
            ) from None

    def check(self, obj: dict, allocation_id: str) -> None:
        labels = obj["metadata"].get("labels", {})
        if any(labels.get(key) != value for key, value in self.labels(allocation_id).items()):
            raise BrowserProviderError("kubernetes_resource_mismatch")

    def manifest(self, allocation_id: str) -> dict:
        name, labels = self.name(allocation_id), self.labels(allocation_id)
        security = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
        storage = {
            "accessModes": [self.cluster.access_mode],
            "resources": {"requests": {"storage": self.cluster.storage_size}},
        }
        if self.cluster.storage_class is not None:
            storage["storageClassName"] = self.cluster.storage_class
        return {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {"name": name, "labels": labels},
            "spec": {
                "serviceName": self.cluster.service_name,
                "replicas": 0,
                "persistentVolumeClaimRetentionPolicy": {"whenScaled": "Retain", "whenDeleted": "Delete"},
                "selector": {"matchLabels": labels},
                "volumeClaimTemplates": [{"metadata": {"name": "profile", "labels": labels}, "spec": storage}],
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "fsGroup": 1000,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "browser",
                                "image": self.cluster.image,
                                "imagePullPolicy": self.cluster.image_pull_policy,
                                "args": ["--root", "/home/browser/data/runtime", "--credential-file", CREDENTIAL],
                                "securityContext": security,
                                "resources": {"requests": self.cluster.requests, "limits": self.cluster.limits},
                                "ports": [
                                    {"name": "cdp", "containerPort": 9222},
                                    {"name": "viewer", "containerPort": 6081},
                                ],
                                "readinessProbe": {"httpGet": {"path": "/ready", "port": "cdp"}, "periodSeconds": 2},
                                "volumeMounts": [
                                    {"name": "profile", "mountPath": "/home/browser/data"},
                                    {"name": "credential", "mountPath": "/run/a13n-browser", "readOnly": True},
                                    {"name": "shm", "mountPath": "/dev/shm"},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "credential", "secret": {"secretName": name, "defaultMode": 0o440}},
                            {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}},
                        ],
                    },
                },
            },
        }

    async def read_set(self, allocation_id: str, state: State | None) -> dict | None:
        obj = await self.call("sets", "GET", self.name(allocation_id))
        if obj is not None:
            self.check(obj, allocation_id)
            if state is not None and (state.allocation_id != allocation_id or obj["metadata"]["uid"] != state.uid):
                raise BrowserProviderError("kubernetes_statefulset_replaced")
        return obj

    @staticmethod
    def terminal(obj: dict) -> bool:
        meta = obj["metadata"]
        return bool(meta.get("deletionTimestamp") or meta.get("annotations", {}).get(TERMINAL))

    @staticmethod
    def generation(obj: dict) -> int:
        return int(obj["metadata"].get("annotations", {}).get(GENERATION, "0"))

    @staticmethod
    def scaled_down(obj: dict) -> bool:
        status = obj.get("status", {})
        return (
            obj["spec"]["replicas"] == 0
            and status.get("replicas", 0) == 0
            and status.get("observedGeneration", 0) >= obj["metadata"]["generation"]
        )

    async def scale(self, obj: dict, replicas: int, generation: int, *, terminal: bool = False) -> None:
        meta = obj["metadata"]
        annotations = {GENERATION: str(generation)}
        if terminal:
            annotations[TERMINAL] = "true"
        await self.call(
            "sets",
            "PATCH",
            meta["name"],
            {
                "metadata": {
                    "uid": meta["uid"],
                    "resourceVersion": meta["resourceVersion"],
                    "annotations": annotations,
                },
                "spec": {"replicas": replicas},
            },
        )

    async def create(self, allocation_id: str, recipe: BaseModel) -> BrowserCheckpoint:
        obj = await self.call("sets", "POST", body=self.manifest(allocation_id))
        self.check(obj, allocation_id)
        return BrowserCheckpoint(State(allocation_id=allocation_id, uid=obj["metadata"]["uid"]).model_dump())

    async def read_child(self, kind: str, state: State, *, owned: bool = True) -> dict | None:
        name = self.name(state.allocation_id)
        name = {"pods": name + "-0", "claims": "profile-" + name + "-0", "secrets": name}[kind]
        obj = await self.call(kind, "GET", name)
        if obj is not None:
            self.check(obj, state.allocation_id)
            if owned and not any(
                ref["uid"] == state.uid and ref["kind"] == "StatefulSet"
                for ref in obj["metadata"].get("ownerReferences", ())
            ):
                raise BrowserProviderError("kubernetes_owner_mismatch")
        return obj

    async def credential(self, state: State, *, create: bool = False) -> str:
        obj = await self.read_child("secrets", state)
        if obj is None and create:
            name = self.name(state.allocation_id)
            obj = await self.call(
                "secrets",
                "POST",
                body={
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "immutable": True,
                    "metadata": {
                        "name": name,
                        "labels": self.labels(state.allocation_id),
                        "ownerReferences": [
                            {
                                "apiVersion": "apps/v1",
                                "kind": "StatefulSet",
                                "name": name,
                                "uid": state.uid,
                            }
                        ],
                    },
                    "data": {"token": base64.b64encode(secrets.token_urlsafe(32).encode()).decode()},
                },
            )
        if obj is None:
            raise BrowserProviderError("kubernetes_credential_missing")
        return base64.b64decode(obj["data"]["token"], validate=True).decode()

    async def reconcile_create(self, allocation_id: str, state: dict | None) -> BrowserCheckpoint | LiveBrowser:
        record = State.model_validate(state) if state is not None else None
        obj = await self.read_set(allocation_id, record)
        if obj is None or self.terminal(obj):
            raise BrowserProviderError("kubernetes_create_unresolved")
        if record is None:
            return BrowserCheckpoint(State(allocation_id=allocation_id, uid=obj["metadata"]["uid"]).model_dump())
        if obj["spec"]["replicas"] == 0:
            if self.generation(obj) != 0:
                raise BrowserProviderError("kubernetes_create_fenced")
            await self.credential(record, create=True)
            await self.scale(obj, 1, 1)
        return await self.wait_running(record)

    async def profile(self, state: State) -> str | None:
        claim = await self.read_child("claims", state)
        uid = claim["metadata"]["uid"] if claim is not None and not claim["metadata"].get("deletionTimestamp") else None
        if state.profile_uid is not None and uid != state.profile_uid:
            raise BrowserProviderError("kubernetes_profile_lost")
        return uid

    @staticmethod
    def pod_host(pod: dict) -> str:
        ip = ip_address(pod["status"]["podIP"])
        return f"[{ip}]" if ip.version == 6 else str(ip)

    async def control(self, host: str, token: str, payload: dict) -> dict:
        # Use only the Kubernetes-observed IP. Do not follow proxies or redirects.
        async with (
            asyncio.timeout(5),
            _ControlSocket(
                f"ws://{host}:9222/control",
                additional_headers={"Authorization": f"Bearer {token}"},
                proxy=None,
                max_size=8192,
                close_timeout=1,
            ) as socket,
        ):
            await socket.send(json.dumps(payload))
            result = json.loads(await socket.recv())
        if not isinstance(result, dict) or "error" in result or result.get("ok") is False:
            raise BrowserProviderError("kubernetes_browser_control_failed")
        return result

    async def observe(self, obj: dict, state: State) -> LiveBrowser | None:
        generation = self.generation(obj)
        pod = await self.read_child("pods", state)
        if pod is None and self.scaled_down(obj):
            return LiveBrowser(
                State(
                    allocation_id=state.allocation_id,
                    uid=state.uid,
                    generation=generation,
                    profile_uid=await self.profile(state),
                ).model_dump(),
                status="stopped",
            )
        if (
            self.terminal(obj)
            or obj["spec"]["replicas"] == 0
            or pod is None
            or pod["metadata"].get("deletionTimestamp")
        ):
            return None
        if not any(c["type"] == "Ready" and c["status"] == "True" for c in pod.get("status", {}).get("conditions", ())):
            return None
        profile_uid = await self.profile(state)
        if profile_uid is None:
            return None
        try:
            description = await self.control(
                self.pod_host(pod), await self.credential(state), {"operation": "describe"}
            )
        except (WebSocketException, OSError, TimeoutError):
            return None
        observed = State(
            allocation_id=state.allocation_id,
            uid=state.uid,
            generation=generation,
            profile_uid=profile_uid,
            pod_uid=pod["metadata"]["uid"],
            runtime_id=description["runtime_id"],
        )
        # Pod UID distinguishes replicas even if a replacement runtime reused its identifier.
        return LiveBrowser(observed.model_dump(), runtime_id=f"{observed.pod_uid}:{observed.runtime_id}")

    async def inspect(self, allocation_id: str, state: dict | None) -> LiveBrowser | None:
        if state is None:
            return None
        record = State.model_validate(state)
        obj = await self.read_set(allocation_id, record)
        if obj is None or self.terminal(obj):
            return None
        live = await self.observe(obj, record)
        if live is None:
            raise BrowserProviderError("kubernetes_browser_pending")
        return live

    async def wait_running(self, state: State) -> LiveBrowser:
        while True:
            obj = await self.read_set(state.allocation_id, state)
            if obj is None or self.terminal(obj) or obj["spec"]["replicas"] != 1:
                raise BrowserProviderError("kubernetes_start_fenced")
            live = await self.observe(obj, state)
            if live is not None and live.status == "ready":
                return live
            await asyncio.sleep(1)

    async def start(self, allocation_id: str, state: dict, *, generation: int) -> LiveBrowser:
        record = State.model_validate(state)
        obj = await self.read_set(allocation_id, record)
        if obj is None or self.terminal(obj):
            raise BrowserProviderError("kubernetes_start_fenced")
        current = self.generation(obj)
        if await self.profile(record) is None:
            raise BrowserProviderError("kubernetes_profile_lost")
        if current < generation and obj["spec"]["replicas"] == 0:
            await self.scale(obj, 1, generation)
        elif current != generation or obj["spec"]["replicas"] != 1:
            raise BrowserProviderError("kubernetes_start_fenced")
        return await self.wait_running(record)

    async def stop(self, allocation_id: str, state: dict) -> LiveBrowser:
        record = State.model_validate(state)
        while True:
            obj = await self.read_set(allocation_id, record)
            if obj is None or self.terminal(obj) or record.generation != self.generation(obj):
                raise BrowserProviderError("kubernetes_stop_fenced")
            if obj["spec"]["replicas"] != 0:
                await self.scale(obj, 0, record.generation)
            else:
                live = await self.observe(obj, record)
                if live is not None:
                    return live
            await asyncio.sleep(1)

    async def delete(self, allocation_id: str, state: dict | None) -> BrowserCheckpoint | None:
        record = State.model_validate(state) if state is not None else None
        obj = await self.read_set(allocation_id, record)
        if obj is None and record is None:
            # The one initial POST might still be in flight. A 404 cannot fence it.
            raise BrowserProviderError("kubernetes_create_unresolved")
        if record is None:
            assert obj is not None
            return BrowserCheckpoint(State(allocation_id=allocation_id, uid=obj["metadata"]["uid"]).model_dump())
        while obj is not None:
            meta = obj["metadata"]
            if not meta.get("deletionTimestamp"):
                if not self.terminal(obj):
                    await self.scale(obj, 0, self.generation(obj), terminal=True)
                elif self.scaled_down(obj) and await self.read_child("pods", record) is None:
                    await self.call(
                        "sets",
                        "DELETE",
                        meta["name"],
                        {
                            "apiVersion": "v1",
                            "kind": "DeleteOptions",
                            "propagationPolicy": "Foreground",
                            "preconditions": {"uid": record.uid, "resourceVersion": meta["resourceVersion"]},
                        },
                    )
            await asyncio.sleep(1)
            obj = await self.read_set(allocation_id, record)
        for kind in ("pods", "claims", "secrets"):
            if await self.read_child(kind, record, owned=False) is not None:
                raise BrowserProviderError("kubernetes_cleanup_pending")
        return None

    async def current_pod(self, state: State) -> dict | None:
        pod = await self.read_child("pods", state)
        return pod if pod is not None and pod["metadata"]["uid"] == state.pod_uid else None

    async def connect(self, state: dict) -> Chromium:
        record = State.model_validate(state)
        pod = await self.current_pod(record)
        if pod is None or not record.runtime_id or pod["metadata"].get("deletionTimestamp"):
            raise BrowserProviderError("kubernetes_browser_changed")
        return await Chromium.connect(
            f"ws://{self.pod_host(pod)}:9222/cdp/{record.runtime_id}",
            headers={"Authorization": f"Bearer {await self.credential(record)}"},
            discover=False,
            use_proxy=False,
        )

    async def viewer_operation(self, state: State, grant_id: str, episode: int, *, expires_at: datetime | None = None):
        pod = await self.current_pod(state)
        if pod is None:
            return None
        token, host = await self.credential(state), self.pod_host(pod)
        description = await self.control(host, token, {"operation": "describe"})
        if description["runtime_id"] != state.runtime_id:
            return None
        payload = {
            "protocol_version": 2,
            "generation": description["viewer_generation"],
            "runtime_id": state.runtime_id,
            "id": grant_id,
            "episode": episode,
            "operation": "revoke_browser",
        }
        if expires_at is not None:
            payload.update(operation="ensure", expires_at=expires_at.timestamp())
        result = (await self.control(host, token, payload))["result"]
        return host, result

    async def ensure_viewer(self, state: dict, grant_id: str, *, episode: int, expires_at: datetime) -> ViewerGrant:
        granted = await self.viewer_operation(State.model_validate(state), grant_id, episode, expires_at=expires_at)
        if granted is None:
            raise BrowserProviderError("kubernetes_browser_changed")
        host, result = granted
        return ViewerGrant(
            f"ws://{host}:6081/?" + urlencode({"token": result["token"]}),
            {"password": result["password"], "protocol": "vnc"},
            datetime.fromtimestamp(result["expires_at"], UTC),
        )

    async def revoke_viewer(self, state: dict, grant_id: str, *, episode: int) -> None:
        await self.viewer_operation(State.model_validate(state), grant_id, episode)

    async def upload_file(self, state: dict, name: str, content: bytes) -> str:
        raise BrowserProviderError("browser_files_not_supported", certainty="not_dispatched")

    async def download_file(self, state: dict, guid: str, *, max_bytes: int) -> bytes:
        raise BrowserProviderError("browser_files_not_supported", certainty="not_dispatched")


class _ControlSocket(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


def identity(config: Configuration, clusters: dict[str, Cluster]) -> dict:
    cluster = clusters.get(config.cluster)
    return {
        "cluster": config.cluster,
        "namespace": config.namespace,
        "profile": profile_fingerprint(cluster) if cluster else None,
        "service": cluster.service_name if cluster else None,
    }


async def open_provider(config: Configuration, clusters: dict[str, Cluster]) -> Kubernetes:
    cluster = clusters.get(config.cluster)
    if cluster is None or config.namespace not in cluster.namespaces:
        raise BrowserProviderError("kubernetes_cluster_not_allowed", certainty="not_dispatched")
    try:
        async with asyncio.timeout(cluster.request_timeout_seconds):
            api = await open_cluster_client(cluster)
    except (ConfigException, OSError, TimeoutError):
        raise BrowserProviderError("kubernetes_authentication_unavailable", certainty="not_dispatched") from None
    return Kubernetes(config, cluster, api)
