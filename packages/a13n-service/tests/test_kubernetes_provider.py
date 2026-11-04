"""Native resource reconciliation contracts; real cluster coverage lives in the kind workflow."""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.kubernetes.configuration import (
    CREDENTIAL_VOLUME,
    ClusterProfile,
    KubernetesConnectionConfiguration,
    KubernetesState,
)
from a13n_harness.providers.environment.kubernetes.runtime import KubernetesRuntime
from a13n_service.providers.environment.management import Completed, Pending
from a13n_service.providers.environment.recipes.kubernetes import KubernetesRecipe
from a13n_service.providers.environments.kubernetes import kubernetes
from a13n_service.providers.environments.kubernetes.management import KubernetesManagement
from a13n_service.providers.environments.kubernetes.render import initial, render
from kubernetes_asyncio.client.exceptions import ApiException
from pydantic import SecretStr, ValidationError

pytestmark = pytest.mark.anyio


def recipe(**updates):
    return KubernetesRecipe.model_validate(
        {
            "execution_container": "agent",
            "pod_template": {"spec": {"containers": [{"name": "agent", "image": "sandbox:test"}]}},
            **updates,
        }
    )


@pytest.fixture
def native():
    account = KubernetesConnectionConfiguration(cluster="test", namespace="sandboxes")
    account._cluster = ClusterProfile(namespaces=("sandboxes",), ownership_id="test-suite", allow_plaintext_envd=True)
    account._bootstrap = SecretStr("private-test-token")
    objects = {}
    requests = []
    behavior = SimpleNamespace(fail_after_create=False, finalizers=False)

    async def call(path, method, **kwargs):
        requests.append((method, path, deepcopy(kwargs.get("body"))))
        if method == "GET":
            if path.endswith("/events"):
                return {"items": [], "metadata": {}}
            if path not in objects:
                raise ApiException(status=404)
            return deepcopy(objects[path])
        if method == "POST":
            body = deepcopy(kwargs["body"])
            key = path + "/" + body["metadata"]["name"]
            if key in objects:
                raise ApiException(status=409)
            body["metadata"]["uid"] = "uid-" + str(len(requests))
            if body["kind"] == "Pod":
                body["status"] = {"phase": "Pending"}
            objects[key] = body
            if behavior.fail_after_create:
                behavior.fail_after_create = False
                raise TimeoutError
            return deepcopy(body)
        if method == "DELETE":
            if path not in objects:
                raise ApiException(status=404)
            assert kwargs["body"]["preconditions"]["uid"] == objects[path]["metadata"]["uid"]
            if behavior.finalizers:
                objects[path]["metadata"].update(deletionTimestamp="now", finalizers=["third-party/protect"])
            else:
                del objects[path]
            return {}
        raise AssertionError(method)

    runtime = KubernetesRuntime(account, SimpleNamespace(call_api=call, close=AsyncMock()))
    return SimpleNamespace(
        runtime=runtime, manager=KubernetesManagement(runtime), objects=objects, requests=requests, behavior=behavior
    )


async def step(native, config, state=None):
    return await native.manager.create(config, environment_id="env_test", operation_id="operation_test", state=state)


async def allocated(native, config):
    outcome = await step(native, config)
    for _ in range(20):
        assert isinstance(outcome, Pending)
        state = KubernetesState.model_validate(outcome.state.state)
        if state.pod_uid:
            return state
        outcome = await step(native, config, outcome.state)
    raise AssertionError("Resources did not converge")


def test_native_recipe_preserves_settings_and_injects_only_owned_bindings(native):
    config = recipe(
        pod_template={
            "metadata": {"labels": {"app": "agent"}},
            "spec": {
                "nodeSelector": {"gpu": "true"},
                "securityContext": {"runAsUser": 0},
                "initContainers": [{"name": "prepare", "image": "busybox", "command": ["true"]}],
                "containers": [
                    {
                        "name": "agent",
                        "image": "sandbox:test",
                        "envFrom": [{"secretRef": {"name": "input"}}],
                        "resources": {"limits": {"nvidia.com/gpu": "1"}},
                    },
                    {"name": "sidecar", "image": "helper"},
                ],
                "volumes": [{"name": "temporary", "emptyDir": {"medium": "Memory"}}],
            },
        }
    )
    before = config.model_dump()
    state = initial(config, native.runtime.connection, "env_test")
    pod = render(config, native.runtime.profile, state, state.resources[-1])
    assert pod["spec"]["nodeSelector"] == {"gpu": "true"}
    assert pod["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert pod["spec"]["containers"][1] == {"name": "sidecar", "image": "helper"}
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["volumes"][-1]["secret"]["defaultMode"] == 0o400
    assert pod["spec"]["containers"][0]["volumeMounts"][-1] == {
        "name": CREDENTIAL_VOLUME,
        "mountPath": "/var/lib/a13n-envd/credential/token",
        "subPath": "token",
        "readOnly": True,
    }
    assert any(r.kind == "Secret" and r.disposition == "borrowed" and r.name == "input" for r in state.resources)
    assert config.model_dump() == before
    assert "private-test-token" not in str(pod) + state.model_dump_json() + config.model_dump_json()


@pytest.mark.parametrize(
    "spec",
    [
        {"serviceAccountName": "manager"},
        {"volumes": [{"name": CREDENTIAL_VOLUME, "emptyDir": {}}]},
        {"containers": [{"name": "agent", "image": "test", "workingDir": "/tmp"}]},
        {"containers": [{"name": "agent", "env": [{"name": "A13N_ENVD_DEVICE_ID", "value": "wrong"}]}]},
        {"containers": [{"name": "agent", "volumeMounts": [{"name": "data", "mountPath": "/var"}]}]},
    ],
)
def test_reserved_pod_bindings_are_refused(spec):
    with pytest.raises(ValidationError):
        recipe(pod_template={"spec": {"containers": [{"name": "agent", "image": "test"}], **spec}})


@pytest.mark.parametrize(
    "spec",
    [
        {"containers": [{"name": []}]},
        {"initContainers": None},
        {"volumes": [{"name": "data", "secret": []}]},
        {"volumes": [{"name": "data", "projected": {"sources": "bad"}}]},
        {"containers": [{"name": "agent", "volumeMounts": [{}]}]},
        {"containers": [{"name": "agent", "env": [{"name": 1}]}]},
        {"containers": [{"name": "agent", "envFrom": [{"secretRef": "bad"}]}]},
    ],
)
def test_invalid_native_shapes_fail_recipe_validation(spec):
    with pytest.raises(ValidationError):
        recipe(pod_template={"spec": {"containers": [{"name": "agent", "image": "test"}], **spec}})


async def test_operational_profile_edits_preserve_identity_but_creation_defaults_do_not(native):
    target = await allocated(native, recipe())
    native.runtime.profile = native.runtime.profile.model_copy(
        update={"request_timeout_seconds": 25, "kubectl": "/usr/bin/kubectl", "allow_port_forward": True}
    )
    native.runtime.validate(target)
    native.runtime.profile = native.runtime.profile.model_copy(update={"sandbox_service_account": "different"})
    with pytest.raises(EnvironmentProviderError) as changed:
        native.runtime.validate(target)
    assert changed.value.code == "kubernetes_profile_changed"


async def test_borrowed_identity_and_shared_directory_survive_retirement(native):
    config = recipe(
        storage=[{"name": "data", "mount_path": "/data", "existing_claim": "shared", "shared_directory": True}],
        pod_template={
            "spec": {
                "containers": [
                    {
                        "name": "agent",
                        "image": "test",
                        "envFrom": [{"secretRef": {"name": "optional", "optional": True}}],
                    }
                ]
            }
        },
    )
    path = native.runtime.path("PersistentVolumeClaim", "shared")
    native.objects[path] = {"metadata": {"name": "shared", "uid": "borrowed-uid"}}
    target = await allocated(native, config)
    borrowed = next(r for r in target.resources if r.name == "shared")
    assert borrowed.uid == "borrowed-uid" and borrowed.sub_paths == (target.pod_name,)
    outcome = await native.manager.destroy(
        config, environment_id="env_test", operation_id="delete", state=target.envelope()
    )
    for _ in range(10):
        if isinstance(outcome, Completed):
            break
        outcome = await native.manager.destroy(
            config, environment_id="env_test", operation_id="delete", state=outcome.state
        )
    assert isinstance(outcome, Completed)
    final = KubernetesState.model_validate(outcome.state.state)
    assert borrowed in final.resources and path in native.objects
    assert not any(method == "DELETE" and target_path == path for method, target_path, _ in native.requests)


def test_operator_profile_is_not_tenant_authentication(native):
    definition = kubernetes({"test": native.runtime.profile})
    definition.validate_domain()
    assert definition.supports_destroy and not definition.supports_stop and not definition.requires_keepalive
    account = definition.configuration_model.model_validate({"cluster": "test", "namespace": "sandboxes"})
    assert account._cluster == native.runtime.profile
    assert "kubeconfig" not in str(account.model_json_schema())
    with pytest.raises(ValidationError):
        definition.configuration_model.model_validate({"cluster": "test", "namespace": "other"})


async def test_create_freezes_names_before_dispatch_then_waits_for_envd(native, monkeypatch):
    config = recipe()
    first = await step(native, config)
    assert isinstance(first, Pending) and first.settled and native.requests == []
    target = await allocated(native, config)
    assert len(native.objects) == 2
    pending = await step(native, config, target.envelope())
    assert pending.state.state["stage"] == "pod_pending"
    pod = native.objects[native.runtime.path("Pod", target.pod_name)]
    pod["status"] = {"phase": "Running", "podIP": "10.1.2.3"}
    binding = SimpleNamespace(open=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(
        import_module("a13n_service.providers.environments.kubernetes.management"), "execution", lambda *args: binding
    )
    pending = await step(native, config, pending.state)
    binding.open.assert_not_awaited()
    done = await step(native, config, pending.state)
    assert isinstance(done, Completed) and done.state.state["stage"] == "ready"
    binding.open.assert_awaited_once()
    binding.close.assert_awaited_once()
    assert [r[0] for r in native.requests].count("POST") == 2


async def test_ambiguous_create_reconciles_same_uid_and_token(native):
    config = recipe()
    first = await step(native, config)
    native.behavior.fail_after_create = True
    with pytest.raises(EnvironmentProviderError) as caught:
        await step(native, config, first.state)
    assert caught.value.certainty == "unknown"
    secret = next(iter(native.objects.values()))
    resumed = await step(native, config, first.state)
    assert resumed.state.state["resources"][0]["uid"] == secret["metadata"]["uid"]
    assert secret["stringData"]["token"] == "private-test-token"
    assert [r[0] for r in native.requests].count("POST") == 1


async def test_confirmed_pod_loss_never_recreates_or_deletes_replacement(native):
    config = recipe()
    target = await allocated(native, config)
    path = native.runtime.path("Pod", target.pod_name)
    replacement = native.objects[path]
    replacement["metadata"]["uid"] = "replacement"
    with pytest.raises(EnvironmentProviderError) as caught:
        await step(native, config, target.envelope())
    assert caught.value.category == "missing"
    assert (await native.manager.inspect(config, environment_id="env_test", state=target.envelope())).status == "absent"
    result = await native.manager.destroy(
        config, environment_id="env_test", operation_id="delete", state=target.envelope()
    )
    assert isinstance(result, Pending)
    assert path in native.objects and native.objects[path]["metadata"]["uid"] == "replacement"
    assert not any(method == "DELETE" and name == path for method, name, _ in native.requests)


async def test_claim_before_consumer_does_not_wait_for_bound_and_retention_is_independent(native):
    config = recipe(
        storage=[
            {
                "name": "keep",
                "mount_path": "/data",
                "claim_template": {
                    "spec": {
                        "storageClassName": "wait-for-consumer",
                        "accessModes": ["ReadWriteOnce"],
                        "resources": {"requests": {"storage": "1Gi"}},
                    }
                },
            },
            {
                "name": "discard",
                "mount_path": "/scratch",
                "disposition": "delete",
                "claim_template": {
                    "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}
                },
            },
            {"name": "shared", "mount_path": "/shared", "existing_claim": "team-data", "shared_directory": True},
        ]
    )
    target = await allocated(native, config)
    created = [body["kind"] for method, _, body in native.requests if method == "POST"]
    assert created == ["Secret", "PersistentVolumeClaim", "PersistentVolumeClaim", "Pod"]
    pod = native.objects[native.runtime.path("Pod", target.pod_name)]
    assert pod["spec"]["containers"][0]["volumeMounts"][-1]["subPath"] == target.pod_name
    state = target.envelope()
    for _ in range(10):
        result = await native.manager.destroy(config, environment_id="env_test", operation_id="delete", state=state)
        state = result.state
        if isinstance(result, Completed):
            break
    assert isinstance(result, Completed)
    assert len(native.objects) == 1
    assert next(iter(native.objects.values()))["metadata"]["name"].endswith("-keep")
    assert any(r["disposition"] == "retain" and r["uid"] for r in state.state["resources"])
    assert not any("team-data" in path and method != "GET" for method, path, _ in native.requests)


async def test_finalizers_hold_deletion_and_are_never_removed(native):
    config = recipe()
    target = await allocated(native, config)
    native.behavior.finalizers = True
    for _ in range(3):
        result = await native.manager.destroy(
            config, environment_id="env_test", operation_id="delete", state=target.envelope()
        )
        assert isinstance(result, Pending)
    assert [method for method, _, _ in native.requests].count("DELETE") == 1
    assert len(native.objects) == 2  # Secret remains while the consumer Pod is terminating.


@pytest.mark.parametrize(
    "status,category,certainty", [(403, "denied", "known"), (500, "unavailable", "unknown"), (422, "invalid", "known")]
)
async def test_api_errors_preserve_certainty_and_never_infer_absence(native, status, category, certainty):
    native.runtime.api.call_api = AsyncMock(side_effect=ApiException(status=status, reason="private server detail"))
    with pytest.raises(EnvironmentProviderError) as caught:
        await native.runtime.create("Pod", {})
    assert (caught.value.category, caught.value.certainty) == (category, certainty)
    assert "private server detail" not in str(caught.value)
    with pytest.raises(EnvironmentProviderError):
        await native.runtime.get("Pod", "missing")


async def test_sdk_raw_response_and_auth_refresh_contract(native):
    from kubernetes_asyncio.client import ApiClient, Configuration

    settings = Configuration()
    settings.host = "https://cluster.example"
    settings.api_key["BearerToken"] = "Bearer initial"
    refresh = AsyncMock(side_effect=lambda config: config.api_key.update({"BearerToken": "Bearer refreshed"}))
    settings.refresh_api_key_hook = refresh
    async with ApiClient(settings) as client:
        response = SimpleNamespace(
            status=200,
            data=b'{"metadata":{"uid":"exact"}}',
            getheaders=lambda: {},
            getheader=lambda name: "application/json",
        )
        client.request = AsyncMock(return_value=response)
        native.runtime.api = client
        assert await native.runtime.get("Pod", "sandbox") == {"metadata": {"uid": "exact"}}
        refresh.assert_awaited_once()
        assert client.request.call_args.kwargs["headers"]["authorization"] == "Bearer refreshed"
        assert client.request.call_args.kwargs["_request_timeout"] == native.runtime.profile.request_timeout_seconds


async def test_service_freezes_private_bootstrap_retries_and_retirement_receipt(service, native, monkeypatch):
    from dataclasses import replace

    from a13n_service.distribution import OSS
    from a13n_service.infra.db import transaction
    from a13n_service.providers.registry import Registry
    from a13n_service.runs.environments import bootstrap, lifecycle
    from a13n_service.runs.environments.tables import EnvironmentRow

    from .environments_support import act, environment, reserve, with_fake_providers

    env = await with_fake_providers(service)
    definition = kubernetes({"test": native.runtime.profile})
    env.runtime = replace(
        env.runtime,
        registry=Registry.of((*[p for p in OSS.providers if p.type != "kubernetes"], definition)),
    )
    env.app.state.runtime = env.runtime
    opened_tokens = []

    async def open_native(*, configuration, credential):
        assert credential is None
        assert configuration._bootstrap is not None
        opened_tokens.append(configuration._bootstrap.get_secret_value())
        return KubernetesRuntime(configuration, native.runtime.api)

    monkeypatch.setattr(import_module("a13n_service.providers.environments.kubernetes"), "open_runtime", open_native)
    monkeypatch.setattr(
        import_module("a13n_service.providers.environments.kubernetes.management"),
        "execution",
        lambda *args: SimpleNamespace(open=AsyncMock(), close=AsyncMock()),
    )
    response = await env.client.post(
        f"{env.api}/environment-providers",
        json={"type": "kubernetes", "name": "Cluster", "config": {"cluster": "test", "namespace": "sandboxes"}},
    )
    assert response.status_code == 201, response.text
    config = recipe(
        storage=[
            {
                "name": "data",
                "mount_path": "/data",
                "disposition": "retain",
                "claim_template": {
                    "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}
                },
            }
        ]
    )
    response = await env.client.post(
        f"{env.api}/environment-templates",
        json={
            "name": "Pod",
            "provider_id": response.json()["id"],
            "config": {"recipe": config.model_dump(mode="json")},
        },
    )
    assert response.status_code == 201, response.text
    instance = await reserve(env, response.json()["id"])
    identifier = instance["id"]

    async def claim_next():
        async with transaction(env.runtime.storage) as session:
            row = await session.get_one(EnvironmentRow, identifier)
            row.next_operation_at = None
        operation = await lifecycle.claim(env.runtime, identifier, owner="worker")
        assert operation is not None
        return operation

    operation = await claim_next()
    token = operation.target.bootstrap.get_secret_value()
    assert token not in repr(operation.target)
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identifier)
        sealed = deepcopy(row.envd_bootstrap)
        assert token not in str(row.handle) + str(sealed)
        assert bootstrap.restore(env.runtime.keys, row).get_secret_value() == token
    outcome = await lifecycle.perform(env.runtime, operation)
    assert outcome.pending and native.requests == []
    await lifecycle.publish(env.runtime, operation, outcome)
    native.behavior.fail_after_create = True
    operation = await claim_next()
    failed = await lifecycle.perform(env.runtime, operation)
    assert failed.fault is not None and failed.fault.certainty == "unknown"
    await lifecycle.publish(env.runtime, operation, failed)
    # A denied reconciliation read proves nothing about the prior create.
    call_api = native.runtime.api.call_api
    native.runtime.api.call_api = AsyncMock(side_effect=ApiException(status=403))
    denied = await claim_next()
    await lifecycle.publish(env.runtime, denied, await lifecycle.perform(env.runtime, denied))
    unknown = await environment(env, identifier)
    assert unknown["failure"]["certainty"] == "unknown" and not unknown["failure"]["permanent"]
    assert (await act(env, "DELETE", identifier))[0] == 409
    native.runtime.api.call_api = call_api
    retry = await claim_next()
    assert retry.operation_id == operation.operation_id and retry.target.bootstrap.get_secret_value() == token
    await lifecycle.publish(env.runtime, retry, await lifecycle.perform(env.runtime, retry))

    for _ in range(12):
        for value in native.objects.values():
            if value["kind"] == "Pod":
                value["status"] = {"phase": "Running", "podIP": "10.1.2.3"}
        if (await environment(env, identifier))["status"] == "ready":
            break
        operation = await claim_next()
        result = await lifecycle.perform(env.runtime, operation)
        assert result.fault is None
        await lifecycle.publish(env.runtime, operation, result)
    ready = await environment(env, identifier)
    assert ready["status"] == "ready"
    assert "envd_bootstrap" not in ready and token not in str(ready)
    assert len([r for r in native.requests if r[0] == "POST" and r[2]["kind"] == "Secret"]) == 1
    diagnostic = await env.client.get(f"{env.api}/environments/{identifier}/kubernetes/diagnostics")
    assert diagnostic.status_code == 200, diagnostic.text
    assert diagnostic.json()["native"]["pod_uid"]
    assert diagnostic.json()["native"]["envd"] == "previously_ready"
    assert token not in diagnostic.text
    assert (await environment(env, identifier))["last_used_at"] == ready["last_used_at"]
    schema = env.app.openapi()["paths"]
    assert schema["/api/v1/environments/{environment_id}/kubernetes/diagnostics"]["get"]["x-a13n-mcp"]
    assert schema["/api/v1/environments/{environment_id}/kubernetes/logs"]["get"]["x-a13n-mcp"]
    # Authorization must finish before opening a cluster client, for both native surfaces.
    accounts = f"{env.workspace}/service-accounts"
    viewer = await env.client.post(accounts, json={"name": "Observer", "role": "viewer"})
    assert viewer.status_code == 201
    key = await env.client.post(f"{accounts}/{viewer.json()['id']}/keys", json={"name": "Observation"})
    assert key.status_code == 201
    unrelated = await env.client.post(
        f"{env.api}/organizations/{env.tenant.organization_id}/workspaces", json={"name": "Unrelated"}
    )
    assert unrelated.status_code == 201
    with monkeypatch.context() as denied:
        opening = AsyncMock(side_effect=AssertionError("unauthorized native I/O"))
        denied.setattr(import_module("a13n_service.runs.environments.kubernetes"), "open_provider", opening)
        for surface in ("diagnostics", "logs"):
            path = f"{env.api}/environments/{identifier}/kubernetes/{surface}"
            response = await env.client.get(path, headers={"authorization": "Bearer " + key.json()["secret"]})
            assert response.status_code == 403, response.text
            response = await env.client.get(path, headers={"x-workspace-id": unrelated.json()["id"]})
            assert response.status_code == 404, response.text
        opening.assert_not_awaited()
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identifier)
        assert row.envd_bootstrap == sealed
    status, deleted = await act(env, "DELETE", identifier)
    assert status == 202 and deleted["status"] == "deleting"
    for _ in range(12):
        operation = await claim_next()
        result = await lifecycle.perform(env.runtime, operation)
        assert result.fault is None
        await lifecycle.publish(env.runtime, operation, result)
        if not result.pending:
            break
    retired = await environment(env, identifier)
    assert retired["status"] == "deleted"
    receipt = KubernetesState.model_validate(retired["retirement_state"]["state"])
    assert any(r.kind == "PersistentVolumeClaim" and r.disposition == "retain" and r.uid for r in receipt.resources)
    assert [v["kind"] for v in native.objects.values()] == ["PersistentVolumeClaim"]
    async with transaction(env.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identifier)
        assert row.handle is None and row.envd_bootstrap is None
    assert set(opened_tokens) == {token}


async def test_session_recovery_uses_pod_identity_not_forwarded_port(native, monkeypatch):
    from contextlib import asynccontextmanager

    from a13n_envd_client.eip.v1 import SessionDescriptor
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery
    from a13n_harness.providers.environment.kubernetes import http
    from a13n_harness.providers.environment.remote_envd.configuration import (
        HttpEnvdConnectionConfiguration,
        HttpEnvdCredential,
    )
    from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime

    config = recipe(connection="port_forward")
    native.runtime.profile = native.runtime.profile.model_copy(update={"allow_port_forward": True})
    native.runtime.connection._cluster = native.runtime.profile
    target = await allocated(native, config)
    descriptor = SessionDescriptor.model_construct(
        device_id=target.environment_id,
        generation="generation",
        session_id="original-session",
        available_methods=tuple(http.REQUIRED_METHODS),
        egress=None,
        boundary=None,
    )
    opened = AsyncMock()
    opened.descriptor = descriptor
    opened.session_id = descriptor.session_id
    opened.__aenter__.return_value = opened
    device = SimpleNamespace(
        descriptor=SimpleNamespace(device_id=target.environment_id, generation="generation", boundary=None),
        open_session=AsyncMock(return_value=opened),
        attach_session=AsyncMock(return_value=opened),
    )
    addresses = iter(("http://127.0.0.1:41001", "http://127.0.0.1:41002"))

    @asynccontextmanager
    async def carrier(*args, **kwargs):
        runtime = HttpEnvdProviderRuntime(
            HttpEnvdConnectionConfiguration(endpoint=next(addresses), allow_plaintext_private_link=True),
            HttpEnvdCredential(token="test-token"),
        )
        yield runtime

    monkeypatch.setattr(HttpEnvdProviderRuntime, "acquire_device", AsyncMock(return_value=device))
    monkeypatch.setattr(http, "http_runtime", carrier)
    first = EnvdSessionRecovery()
    async with http.session(native.runtime, target, config, session_recovery=first):
        checkpoint = first.state
        assert checkpoint is not None and target.pod_uid in checkpoint.target
        assert "127.0.0.1" not in checkpoint.target
    resumed = EnvdSessionRecovery(checkpoint)
    async with http.session(native.runtime, target, config, session_recovery=resumed):
        assert resumed.state.descriptor.session_id == "original-session"
    device.open_session.assert_awaited_once()
    device.attach_session.assert_awaited_once_with(descriptor)


@pytest.mark.parametrize("exit_path", ["normal", "cancel", "uid_change", "failure", "timeout"])
async def test_port_forward_owns_process_through_failure_and_cancellation(native, monkeypatch, exit_path):
    import asyncio
    import sys
    from pathlib import Path

    import anyio
    from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
    from a13n_harness.environment.errors import provider_error
    from a13n_harness.providers.environment.kubernetes.http import port_forward

    target = await allocated(native, recipe())
    native.runtime.profile = ClusterProfile(
        authentication="kubeconfig",
        kubeconfig=Path("/trusted/config"),
        context="explicit",
        namespaces=("sandboxes",),
        ownership_id="test",
        allow_port_forward=True,
        request_timeout_seconds=0.1 if exit_path == "timeout" else 5,
    )
    check = AsyncMock(return_value={})
    if exit_path == "uid_change":
        check.side_effect = [{}, provider_error("kubernetes", "kubernetes_uid_changed", Category.MISSING)]
    monkeypatch.setattr(native.runtime, "pod", check)
    launch = asyncio.create_subprocess_exec
    processes = []
    commands = []

    async def start_process(*args, **kwargs):
        commands.append(args)
        program = "import time; print('Forwarding from 127.0.0.1:41001 -> 8888', flush=True); time.sleep(60)"
        if exit_path == "failure":
            program = "raise SystemExit(1)"
        elif exit_path == "timeout":
            program = "import time; time.sleep(60)"
        process = await launch(sys.executable, "-c", program, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start_process)
    if exit_path in {"failure", "uid_change", "timeout"}:
        with pytest.raises((EnvironmentProviderError, TimeoutError)):
            async with port_forward(native.runtime, target, 8888):
                pytest.fail("Failed preparation must not publish an endpoint")
    else:
        with anyio.CancelScope() as scope:
            async with port_forward(native.runtime, target, 8888) as address:
                assert address == "http://127.0.0.1:41001"
                assert processes[0].returncode is None
                if exit_path == "cancel":
                    scope.cancel()
                    await anyio.sleep(0)
    assert len(processes) == 1 and processes[0].returncode is not None
    assert commands[0][-4:] == ("--address", "127.0.0.1", f"pod/{target.pod_name}", ":8888")
    assert "--context" in commands[0] and "--kubeconfig" in commands[0]


async def test_diagnostics_are_read_only_redacted_and_distinguish_absence(native):
    from a13n_service.providers.environments.kubernetes.diagnostics import inspect

    config = recipe(
        pod_template={
            "metadata": {"annotations": {"private": "annotation-value"}},
            "spec": {
                "containers": [
                    {
                        "name": "agent",
                        "image": "sandbox:test",
                        "args": ["literal-argument"],
                        "env": [{"name": "TOKEN", "value": "literal-environment"}],
                    }
                ],
            },
        }
    )
    target = await allocated(native, config)
    path = native.runtime.path("Pod", target.pod_name)
    native.objects[path]["status"] = {
        "phase": "Pending",
        "conditions": [
            {
                "type": "PodScheduled",
                "status": "False",
                "reason": "Unschedulable",
                "message": "private-test-token cannot schedule",
            }
        ],
    }
    native.objects[path]["metadata"].update(deletionTimestamp="2026-11-04T00:00:00Z", finalizers=["example/protect"])
    native.requests.clear()
    result = await inspect(native.runtime, config, target)
    assert result.resources[-1].status["phase"] == "Pending"
    assert result.resources[-1].finalizers == ["example/protect"]
    assert result.envd == "not_verified"
    for secret in ("private-test-token", "literal-argument", "literal-environment", "annotation-value"):
        assert secret not in result.model_dump_json()
    assert all(method == "GET" for method, _, _ in native.requests)
    del native.objects[path]
    assert (await inspect(native.runtime, config, target)).resources[-1].observation == "absent"


async def test_diagnostics_denied_is_not_absent_and_events_are_uid_scoped(native):
    from a13n_service.providers.environments.kubernetes.diagnostics import inspect

    config = recipe()
    target = await allocated(native, config)
    call = native.runtime.api.call_api

    async def api(path, method, **kwargs):
        if path.endswith("/events"):
            assert dict(kwargs["query_params"])["fieldSelector"] == f"involvedObject.uid={target.pod_uid}"
            return {
                "metadata": {"continue": "next"},
                "items": [
                    {
                        "involvedObject": {"uid": target.pod_uid},
                        "reason": "FailedMount",
                        "message": "private-test-token",
                    },
                    {"involvedObject": {"uid": "unrelated"}, "message": "do-not-return"},
                ],
            }
        return await call(path, method, **kwargs)

    native.runtime.api.call_api = api
    result = await inspect(native.runtime, config, target)
    assert result.events_truncated
    assert len(result.events) == 1 and result.events[0].reason == "FailedMount"
    assert result.events[0].message == "[redacted]"
    native.runtime.api.call_api = AsyncMock(side_effect=ApiException(status=403))
    result = await inspect(native.runtime, config, target)
    assert all(item.observation == "unknown" for item in result.resources)


async def test_logs_are_bounded_redacted_and_reject_replacement(native):
    from a13n_service.providers.environments.kubernetes.diagnostics import LogQuery, logs

    config = recipe()
    target = await allocated(native, config)
    call = native.runtime.api.call_api
    replace = False

    async def api(path, method, **kwargs):
        if path.endswith("/log"):
            params = dict(kwargs["query_params"])
            assert params["limitBytes"] == 65536 and params["tailLines"] == 25
            assert params["previous"] == "true"
            assert kwargs["header_params"]["Accept"] == "*/*"
            assert kwargs["response_types_map"][200] == "str"
            if replace:
                native.objects[native.runtime.path("Pod", target.pod_name)]["metadata"]["uid"] = "replacement"
            return "startup private-test-token\n"
        return await call(path, method, **kwargs)

    native.runtime.api.call_api = api
    query = LogQuery(tail_lines=25, previous=True)
    result = await logs(native.runtime, config, target, query)
    assert result.text == "startup [redacted]\n" and result.pod_uid == target.pod_uid
    with pytest.raises(EnvironmentProviderError) as missing:
        await logs(native.runtime, config, target, LogQuery(container="other"))
    assert missing.value.code == "kubernetes_container_not_found"
    replace = True
    with pytest.raises(EnvironmentProviderError) as replaced:
        await logs(native.runtime, config, target, query)
    assert replaced.value.code == "kubernetes_uid_changed"


async def test_provider_preflight_allocates_nothing_and_closes_client(native, monkeypatch):
    from a13n_harness.providers.endpoint_policy import EndpointPolicy
    from a13n_service.providers.registry import Registry
    from a13n_service.resources.providers.probe import probe

    api = AsyncMock(return_value={"items": [], "metadata": {}})
    native.runtime.api.call_api = api
    monkeypatch.setattr(
        import_module("a13n_harness.providers.environment.kubernetes.runtime"),
        "open_runtime",
        AsyncMock(return_value=native.runtime),
    )
    registry = Registry.of((kubernetes({"test": native.runtime.profile}),))
    kwargs = dict(
        config={"cluster": "test", "namespace": "sandboxes"},
        credential=None,
        extra_headers={},
        policy=EndpointPolicy(),
        timeout=5,
        max_bytes=65536,
    )
    result = await probe(registry, "environment", "kubernetes", **kwargs)
    assert result.status == "succeeded" and "smoke environment" in result.message
    assert api.call_args.args == (native.runtime.path("Pod"), "GET")
    assert api.call_args.kwargs["query_params"] == [("limit", 1)]
    native.runtime.api.close.assert_awaited_once()
    api.side_effect = ApiException(status=403, reason="private-upstream-detail")
    result = await probe(registry, "environment", "kubernetes", **kwargs)
    assert result.status == "failed" and result.message == "kubernetes_api_failed"
    assert native.runtime.api.close.await_count == 2
    api.side_effect = ApiException(status=404, reason="private-upstream-detail")
    result = await probe(registry, "environment", "kubernetes", **kwargs)
    assert result.status == "failed" and result.message == "kubernetes_namespace_unavailable"
    assert native.runtime.api.close.await_count == 3


async def test_native_auth_setup_failure_is_classified_without_configuration_details(native, monkeypatch):
    from a13n_harness.providers.environment.kubernetes.runtime import open_runtime
    from kubernetes_asyncio.config.config_exception import ConfigException

    monkeypatch.setattr(
        "kubernetes_asyncio.config.load_incluster_config",
        lambda **kwargs: (_ for _ in ()).throw(ConfigException("private-file-path")),
    )
    with pytest.raises(EnvironmentProviderError) as failure:
        await open_runtime(configuration=native.runtime.connection, credential=None)
    assert failure.value.code == "kubernetes_authentication_unavailable"
    assert "private-file-path" not in str(failure.value)


def test_kubernetes_catalog_starters_are_valid_native_recipes():
    definition = kubernetes({})
    for name, value in definition.environment_model.model_json_schema()["x-environment-presets"].items():
        recipe = definition.environment_model.model_validate(value)
        assert recipe.connection == name
        assert recipe.pod_template.spec["containers"][0]["image"] == "a13n-sandbox:local"
