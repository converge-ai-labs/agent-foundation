"""Read-only native observations, separate from lifecycle publication and execution."""

from __future__ import annotations

import json
import shlex
from datetime import UTC, datetime
from typing import Any, Literal

from a13n_harness.environment.errors import EnvironmentProviderError, provider_error
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.kubernetes.configuration import KEY, KubernetesState, Resource
from a13n_harness.providers.environment.kubernetes.runtime import KubernetesRuntime, check_resource
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.providers.environment.recipes.kubernetes import KubernetesRecipe

from .render import render


class ResourceObservation(BaseModel):
    reference: Resource
    observation: Literal["present", "absent", "unknown", "conflict"]
    reason: str | None = None
    created_at: str | None = None
    deleting_at: str | None = None
    finalizers: list[str] = Field(default_factory=list)
    status: dict[str, JsonValue] = Field(default_factory=dict)


class Event(BaseModel):
    resource_uid: str
    type: str | None = None
    reason: str | None = None
    message: str | None = None
    count: int | None = None
    first_timestamp: str | None = None
    last_timestamp: str | None = None


class Diagnostics(BaseModel):
    observed_at: datetime
    cluster: str
    namespace: str
    ownership_id: str
    pod_name: str
    pod_uid: str | None
    stage: str
    connection: str
    # This is the last published lifecycle observation, never a new readiness grant.
    envd: Literal["previously_ready", "not_verified"]
    resources: list[ResourceObservation]
    rendered: list[dict[str, JsonValue]]
    events: list[Event]
    events_truncated: bool
    errors: list[str]
    commands: list[str]


class LogQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    container: str | None = None
    previous: bool = False
    tail_lines: int = Field(default=100, ge=1, le=1000)


class Logs(BaseModel):
    pod_uid: str
    container: str
    previous: bool
    text: str
    limit_bytes: int = 65536


def redact(runtime: KubernetesRuntime, value: Any) -> Any:
    """Never export the platform bootstrap, even if a guest prints it in diagnostics."""
    token = runtime.connection._bootstrap
    if token is None:
        return value
    return json.loads(json.dumps(value).replace(token.get_secret_value(), "[redacted]"))


def safe_configuration(value: Any) -> Any:
    """Keep native structure without echoing literal environment/command/annotation payloads."""
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key in {"data", "stringData", "binaryData", "annotations", "command", "args", "value", "httpHeaders"}
            else safe_configuration(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe_configuration(item) for item in value]
    return value


async def inspect(runtime: KubernetesRuntime, recipe: KubernetesRecipe, target: KubernetesState) -> Diagnostics:
    from kubernetes_asyncio.client.exceptions import ApiException

    runtime.validate(target)
    observations: list[ResourceObservation] = []
    events: list[Event] = []
    errors: list[str] = []
    truncated = False
    for reference in target.resources:
        try:
            value = await runtime.get(reference.kind, reference.name)
            if value is None:
                observations.append(ResourceObservation(reference=reference, observation="absent"))
                continue
            check_resource(value, reference, target)
        except EnvironmentProviderError as error:
            observations.append(
                ResourceObservation(
                    reference=reference,
                    observation="conflict" if error.category in {Category.CONFLICT, Category.MISSING} else "unknown",
                    reason=error.code,
                )
            )
            continue
        metadata = value["metadata"]
        # Secret/ConfigMap values and arbitrary metadata are deliberately not returned.
        status = value.get("status", {}) if reference.kind in {"Pod", "PersistentVolumeClaim"} else {}
        observations.append(
            ResourceObservation(
                reference=reference,
                observation="present",
                created_at=metadata.get("creationTimestamp"),
                deleting_at=metadata.get("deletionTimestamp"),
                finalizers=metadata.get("finalizers", []),
                status=redact(runtime, status),
            )
        )
        if reference.kind not in {"Pod", "PersistentVolumeClaim"}:
            continue
        try:
            page = await runtime.call(
                "GET",
                f"/api/v1/namespaces/{target.namespace}/events",
                query=[
                    ("fieldSelector", "involvedObject.uid=" + metadata["uid"]),
                    ("limit", 50),
                ],
            )
            truncated |= bool(page.get("metadata", {}).get("continue"))
            for event in page.get("items", [])[:50]:
                if event.get("involvedObject", {}).get("uid") != metadata["uid"]:
                    continue
                events.append(
                    Event(
                        resource_uid=metadata["uid"],
                        type=event.get("type"),
                        reason=event.get("reason"),
                        message=redact(runtime, event.get("message", ""))[:4096],
                        count=event.get("count"),
                        first_timestamp=event.get("firstTimestamp"),
                        last_timestamp=event.get("lastTimestamp"),
                    )
                )
        except EnvironmentProviderError as error:
            errors.append(error.code)
        except ApiException:
            errors.append("kubernetes_events_unavailable")
    command = ["kubectl"]
    if runtime.profile.context:
        command += ["--context", runtime.profile.context]
    command += ["--namespace", target.namespace]
    return Diagnostics(
        observed_at=datetime.now(UTC),
        cluster=target.cluster,
        namespace=target.namespace,
        ownership_id=target.ownership_id,
        pod_name=target.pod_name,
        pod_uid=target.pod_uid,
        stage=target.stage,
        connection=recipe.connection,
        envd="previously_ready" if target.stage == "ready" else "not_verified",
        resources=observations,
        rendered=[
            safe_configuration(render(recipe, runtime.profile, target, ref))
            for ref in target.resources
            if ref.disposition != "borrowed"
        ],
        events=events,
        events_truncated=truncated,
        errors=errors,
        commands=[
            shlex.join(command + suffix)
            for suffix in [
                ["get", "pod", target.pod_name, "-o", "wide"],
                ["describe", "pod", target.pod_name],
                ["logs", target.pod_name, "-c", recipe.execution_container, "--tail=100"],
            ]
        ],
    )


async def logs(runtime: KubernetesRuntime, recipe: KubernetesRecipe, target: KubernetesState, query: LogQuery) -> Logs:
    from kubernetes_asyncio.client.exceptions import ApiException

    pod = await runtime.pod(target)
    container = query.container or recipe.execution_container
    names = {item["name"] for key in ("containers", "initContainers") for item in pod["spec"].get(key, [])}
    if container not in names:
        raise provider_error(KEY, "kubernetes_container_not_found", Category.INVALID)
    try:
        text = await runtime.call(
            "GET",
            runtime.path("Pod", target.pod_name) + "/log",
            query=[
                ("container", container),
                ("previous", str(query.previous).lower()),
                ("tailLines", query.tail_lines),
                ("limitBytes", 65536),
                ("timestamps", "true"),
            ],
            text=True,
        )
    except ApiException:
        raise provider_error(KEY, "kubernetes_logs_unavailable", Category.UNAVAILABLE) from None
    # The logs API has no UID precondition. Do not return a replacement Pod's output.
    await runtime.pod(target)
    return Logs(
        pod_uid=pod["metadata"]["uid"], container=container, previous=query.previous, text=redact(runtime, text)[:65536]
    )
