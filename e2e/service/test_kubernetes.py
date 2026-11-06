"""Real-cluster storage, failure and recovery journeys, explicitly selected with -m kubernetes."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from a13n_harness.providers.environment.kubernetes.configuration import LABEL_PREFIX

from . import kubernetes
from .api import eventually, expect, message
from .scripted import tool_results
from .test_environments import backend, create_template, delete

pytestmark = [pytest.mark.anyio, pytest.mark.kubernetes, pytest.mark.timeout(600)]


def claim(disposition):
    return {
        "name": "workspace",
        "mount_path": "/workspace",
        "disposition": disposition,
        "claim_template": {"spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}},
    }


async def diagnostics(api, identifier):
    return expect(await api.client.get(f"/api/v1/environments/{identifier}/kubernetes/diagnostics"), 200)["native"]


async def run_command(stack, account, agent, cleanup, marker, command):
    template = await create_template(stack.api, account)
    await stack.model.call("shell_exec", {"command": command}, call_id="call_storage", to=marker)
    await stack.model.say("Stored.", to=marker)
    receipt = await stack.api.start(
        agent,
        marker,
        options={
            "environment": {"mode": "fresh", "template_id": template},
            "execution": {"tools": {"mode": "inline", "foreground_wait_seconds": 60}},
        },
    )
    identifier = receipt["run"]["environment_mounts"][0]["environment_id"]
    cleanup.append(identifier)
    try:
        run = await stack.api.sealed(receipt["run"]["id"], timeout=180)
        assert run["status"] == "completed", run["failure"]
    except (TimeoutError, AssertionError):
        print(await diagnostics(stack.api, identifier))
        raise
    requests = await stack.model.requests(marker)
    assert "storage-ok" in tool_results(requests[-1])[0]
    assert await account.exists(identifier)
    return identifier


async def test_kubernetes_claim_lifecycle_and_retained_shared_reuse(stack, request):
    async with backend("kubernetes", stack.directory, request.config) as base:
        recipe = deepcopy(base.recipe)
        recipe["pod_template"]["spec"]["securityContext"] = {"fsGroup": 1000}
        recipe["storage"] = [
            claim("retain"),
            {**claim("delete"), "name": "scratch", "mount_path": "/scratch"},
        ]
        account = replace(base, recipe=recipe)
        agent = await stack.api.create_agent("Storage", await stack.api.create_model(stack.model.base_url))
        cleanup = []
        shared_cleanup = []
        try:
            # kind's default storage class uses WaitForFirstConsumer.
            identifier = await run_command(
                stack,
                account,
                agent,
                cleanup,
                "[owned]",
                "echo storage-ok > proof.txt && cp proof.txt /scratch/proof.txt && cat /scratch/proof.txt",
            )
            from kubernetes_asyncio.client import CoreV1Api

            async with kubernetes.runtime() as native:
                owned_claims = [
                    r
                    for r in await account.kubernetes_resources(native, identifier)
                    if r["kind"] == "PersistentVolumeClaim"
                ]
                assert len(owned_claims) == 2
                # Operator metadata edits must not strand the Pod's bootstrap Secret.
                for owned_claim in owned_claims:
                    await CoreV1Api(native.api).patch_namespaced_persistent_volume_claim(
                        owned_claim["name"],
                        native.connection.namespace,
                        {
                            "metadata": {
                                "uid": owned_claim["uid"],
                                "annotations": {LABEL_PREFIX + "recipe": "operator-edited"},
                            }
                        },
                        _content_type="application/merge-patch+json",
                    )
            await delete(stack.api, f"/api/v1/environments/{identifier}", 120)
            retired = expect(await stack.api.client.get(f"/api/v1/environments/{identifier}"), 200)
            assert retired["retirement_state"] is not None
            async with kubernetes.runtime() as native:
                remaining = await account.kubernetes_resources(native, identifier)
                # Only the retained claim survives: scratch, Pod and Secret are gone.
                [retained] = remaining
                assert retained["kind"] == "PersistentVolumeClaim"
                saved = await native.get("PersistentVolumeClaim", retained["name"])
                assert saved is not None
                assert saved["status"]["phase"] == "Bound"
                storage_class = await native.call(
                    "GET", "/apis/storage.k8s.io/v1/storageclasses/" + saved["spec"]["storageClassName"]
                )
                assert storage_class["volumeBindingMode"] == "WaitForFirstConsumer"
            # Borrow the retained claim and use an explicit per-instance directory.
            shared = replace(
                account,
                recipe={
                    **deepcopy(recipe),
                    "storage": [
                        {
                            "name": "workspace",
                            "mount_path": "/workspace",
                            "existing_claim": retained["name"],
                            "shared_directory": True,
                        }
                    ],
                },
            )
            # A native second mount also proves that the retained root data survived retirement.
            shared.recipe["pod_template"]["spec"]["containers"][0]["volumeMounts"] = [
                {"name": "workspace", "mountPath": "/retained", "readOnly": True}
            ]
            shared_id = await run_command(
                stack,
                shared,
                agent,
                shared_cleanup,
                "[shared]",
                "test ! -e proof.txt && echo shared > proof.txt && cat /retained/proof.txt",
            )
            await delete(stack.api, f"/api/v1/environments/{shared_id}", 120)
            async with kubernetes.runtime() as native:
                saved = await native.get("PersistentVolumeClaim", retained["name"])
                assert saved is not None
                assert saved["metadata"]["uid"] == retained["uid"]
                assert not await shared.kubernetes_resources(native, shared_cleanup[0])
            receipt = expect(await stack.api.client.get(f"/api/v1/environments/{shared_cleanup[0]}"), 200)
            assert retained["name"] in str(receipt["retirement_state"])
        finally:
            for identifier in shared_cleanup:
                await base.remove(identifier)
            # Explicit test teardown removes only this journey's owned retained claim.
            for identifier in cleanup:
                await account.remove(identifier)


@pytest.mark.parametrize("fault", ["image", "scheduling", "storage", "startup"])
async def test_kubernetes_failed_preparation_has_native_evidence_and_cleans_up(stack, request, fault):
    async with backend("kubernetes", stack.directory, request.config) as base:
        recipe = deepcopy(base.recipe)
        spec = recipe["pod_template"]["spec"]
        if fault == "image":
            spec["containers"][0].update(image="a13n-missing-e2e:never-loaded", imagePullPolicy="Never")
        elif fault == "scheduling":
            spec["nodeSelector"] = {"a13n.test/nonexistent-node": "true"}
        elif fault == "storage":
            recipe["storage"] = [
                {"name": "workspace", "mount_path": "/workspace", "existing_claim": "a13n-missing-e2e-claim"}
            ]
        else:
            spec["containers"][0]["command"] = ["sh", "-c", "echo expected-startup-failure >&2; exit 23"]
        account = replace(base, recipe=recipe)
        identifier = expect(
            await stack.api.client.post(
                "/api/v1/environments", json={"template_id": await create_template(stack.api, account)}
            ),
            201,
        )["id"]
        try:

            async def observed():
                result = await stack.api.client.get(f"/api/v1/environments/{identifier}/kubernetes/diagnostics")
                if result.status_code == 409:
                    return None
                native = expect(result, 200)["native"]
                pods = [r for r in native["resources"] if r["reference"]["kind"] == "Pod"]
                if not pods or not pods[0]["status"]:
                    return None
                status = pods[0]["status"]
                if fault == "startup":
                    # IDs and timestamps can contain "23" before the container has exited.
                    exited = any(
                        container["name"] == recipe["execution_container"]
                        and container["state"].get("terminated", {}).get("exitCode") == 23
                        for container in status.get("containerStatuses", [])
                    )
                    return native if exited else None
                evidence = str(status) + str(native["events"])
                expected = {
                    "image": "ErrImageNeverPull",
                    "scheduling": "Unschedulable",
                    "storage": "not found",
                }[fault]
                return native if expected in evidence else None

            native = await eventually(observed, timeout=90)
            assert native["envd"] == "not_verified"
            assert expect(await stack.api.client.get(f"/api/v1/environments/{identifier}"), 200)["status"] != "ready"
            if fault == "startup":
                logs = expect(await stack.api.client.get(f"/api/v1/environments/{identifier}/kubernetes/logs"), 200)
                assert "expected-startup-failure" in logs["text"]
            await delete(stack.api, f"/api/v1/environments/{identifier}", 120)
            assert not await account.exists(identifier)
        finally:
            await account.remove(identifier)


@pytest.mark.isolated_service(start_workers=1)
async def test_kubernetes_fresh_run_survives_worker_and_control_restart(stack, request):
    import signal

    from .stack import trust, wait_ready
    from .test_recovery import tool_committed

    async with backend("kubernetes", stack.directory, request.config) as account:
        api, model = stack.api, stack.model
        template = await create_template(api, account)
        agent = await api.create_agent("Recovery", await api.create_model(model.base_url))
        options = {
            "environment": {"mode": "fresh", "template_id": template},
            "execution": {"tools": {"mode": "inline", "foreground_wait_seconds": 60}},
        }
        cleanup = []
        try:
            await model.call(
                "shell_exec",
                {"command": "echo once >> proof.txt && cat proof.txt"},
                call_id="call_once",
                to="[recovery]",
            )
            await model.say("Uncommitted.", to="[recovery]", hold="never")
            key = "kubernetes-recovery"
            first = await api.start(agent, "[recovery]", options=options, key=key)
            run_id = first["run"]["id"]
            identifier = first["run"]["environment_mounts"][0]["environment_id"]
            cleanup.append(identifier)
            repeated = expect(
                await api.client.post(
                    "/api/v1/threads",
                    json=message(agent, "[recovery]", options=options),
                    headers={"idempotency-key": key},
                ),
                200,
            )
            assert repeated["run"]["id"] == run_id
            assert repeated["run"]["environment_mounts"] == first["run"]["environment_mounts"]
            await model.arrived("[recovery]", status="held")
            await tool_committed(api, run_id)
            assert await account.exists(identifier)
            original_uid = account.native_uids[identifier]
            doomed, successor = stack.workers
            await stack.start_worker(successor)
            doomed.signal(signal.SIGKILL)
            doomed.wait(5)
            await model.call(
                "shell_exec",
                {"command": "test $(wc -l < proof.txt) = 1 && echo recovered-ok"},
                call_id="call_recovered",
                to="[recovery]",
            )
            await model.say("Recovered.", to="[recovery]")
            run = await api.sealed(run_id, timeout=90)
            assert (run["status"], run["attempts"], run["output"]) == ("completed", 2, "Recovered.")
            assert "recovered-ok" in tool_results((await model.requests("[recovery]"))[-1])[-1]
            assert await account.exists(identifier) and account.native_uids[identifier] == original_uid
            # Control restarts against the same database and observes the same native target.
            stack.control.kill()
            stack.control.start()
            await asyncio.to_thread(wait_ready, [stack.control], trust(stack.stores))
            assert (await diagnostics(api, identifier))["pod_uid"] == original_uid
            assert (
                expect(await api.client.get(f"/api/v1/threads/{first['thread']['id']}/environments"), 200)["items"]
                == []
            )
            await delete(api, f"/api/v1/environments/{identifier}", 120)
        finally:
            for identifier in cleanup:
                await account.remove(identifier)


async def test_kubernetes_deletion_waits_for_finalizer_without_removing_it(stack, request):
    from a13n_harness.providers.environment.kubernetes.configuration import Resource
    from kubernetes_asyncio.client import CoreV1Api
    from kubernetes_asyncio.client.exceptions import ApiException

    async with backend("kubernetes", stack.directory, request.config) as account:
        api = stack.api
        identifier = expect(
            await api.client.post("/api/v1/environments", json={"template_id": await create_template(api, account)}),
            201,
        )["id"]
        finalizer = "e2e.a13n.dev/hold"
        reference = None
        async with kubernetes.runtime() as native:
            core = CoreV1Api(native.api)

            async def release():
                async def remove_finalizer():
                    if reference is None:
                        return True
                    pod = await native.get("Pod", reference.name)
                    if pod is None or pod["metadata"]["uid"] != reference.uid:
                        return True
                    finalizers = pod["metadata"].get("finalizers", [])
                    if finalizer not in finalizers:
                        return True
                    try:
                        await core.patch_namespaced_pod(
                            reference.name,
                            native.connection.namespace,
                            {
                                "metadata": {
                                    "uid": reference.uid,
                                    "resourceVersion": pod["metadata"]["resourceVersion"],
                                    "finalizers": [f for f in finalizers if f != finalizer],
                                }
                            },
                            _content_type="application/merge-patch+json",
                        )
                    except ApiException as exc:
                        if exc.status == 409:
                            # Pod status and garbage collection can advance resourceVersion between GET and PATCH.
                            return False
                        if exc.status != 404:
                            raise
                    return True

                await eventually(remove_finalizer)

            try:

                async def ready():
                    return expect(await api.client.get(f"/api/v1/environments/{identifier}"), 200)["status"] == "ready"

                await eventually(ready, timeout=180)
                details = await diagnostics(api, identifier)
                reference = Resource(kind="Pod", name=details["pod_name"], uid=details["pod_uid"])
                pod = await native.get("Pod", reference.name)
                assert pod is not None
                await core.patch_namespaced_pod(
                    reference.name,
                    native.connection.namespace,
                    {
                        "metadata": {
                            "resourceVersion": pod["metadata"]["resourceVersion"],
                            "finalizers": [*pod["metadata"].get("finalizers", []), finalizer],
                        }
                    },
                )
                path = f"/api/v1/environments/{identifier}"
                saved = await api.client.get(path)
                expect(await api.client.delete(path, headers={"if-match": saved.headers["etag"]}), 202)

                async def blocked():
                    observations = (await diagnostics(api, identifier))["resources"]
                    return next((r for r in observations if r["reference"]["kind"] == "Pod" and r["deleting_at"]), None)

                held = await eventually(blocked, timeout=60)
                assert finalizer in held["finalizers"]
                assert expect(await api.client.get(path), 200)["status"] == "deleting"
                await release()

                async def deleted():
                    return expect(await api.client.get(path), 200)["status"] == "deleted"

                await eventually(deleted, timeout=120)
            finally:
                # Only the finalizer installed by this test is removed, with current resourceVersion and UID.
                await release()
                await account.remove(identifier)
