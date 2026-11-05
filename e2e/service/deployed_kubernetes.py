"""Native Sandbox journey through the existing Helm deployment, invoked by make k8s-smoke.

Only the scripted model is a fixture. Control, Worker, stores, Envd and Kubernetes run in the cluster.
"""

import asyncio
import json
import re
import secrets
from contextlib import asynccontextmanager

import httpx2

from scripts import k8s_local as k8s

from .api import Workspace, eventually, expect
from .scripted import ScriptedModel, tool_results

MODEL_SERVICE = "a13n-scripted-model"
MODEL_URL = f"http://{MODEL_SERVICE}:8000/v1"


def fixture_manifests(image: str, name: str) -> list[dict]:
    """Mount the shared fixture source into the Service image; never package test code in that image."""
    labels = {"a13n.dev/deployment-smoke": name}
    return [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name},
            "data": {
                filename: (k8s.ROOT / "dev/fixtures" / filename).read_text()
                for filename in ("scripted_model.py", "process.py")
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "labels": labels},
            "spec": {
                "automountServiceAccountToken": False,
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "model",
                        "image": image,
                        "imagePullPolicy": "Never",
                        "command": [
                            "python",
                            "-m",
                            "uvicorn",
                            "dev.fixtures.scripted_model:app",
                            "--host",
                            "0.0.0.0",
                            "--port",
                            "8000",
                        ],
                        "env": [{"name": "PYTHONPATH", "value": "/fixture"}],
                        "volumeMounts": [{"name": "source", "mountPath": "/fixture/dev/fixtures", "readOnly": True}],
                        "readinessProbe": {"httpGet": {"path": "/healthz", "port": 8000}, "periodSeconds": 1},
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
                "volumes": [{"name": "source", "configMap": {"name": name}}],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": MODEL_SERVICE},
            "spec": {"selector": labels, "ports": [{"port": 8000, "targetPort": 8000}]},
        },
    ]


@asynccontextmanager
async def scripted_model(image: str):
    name = "a13n-smoke-" + secrets.token_hex(6)
    created = []
    process = None
    model = None
    try:
        for manifest in fixture_manifests(image, name):
            k8s.kubectl("create", "-f", "-", input_text=json.dumps(manifest))
            created.append(f"{manifest['kind']}/{manifest['metadata']['name']}")
        k8s.kubectl("wait", "--for=condition=Ready", f"pod/{name}", "--timeout=120s")
        # Only test controls use a tunnel; Worker -> model and Worker -> Envd are direct cluster traffic.
        process = await asyncio.create_subprocess_exec(
            "kubectl",
            "--context",
            k8s.CONTEXT,
            "-n",
            k8s.NAMESPACE,
            "port-forward",
            "--address",
            "127.0.0.1",
            f"pod/{name}",
            ":8000",
            stdout=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        async with asyncio.timeout(30):
            line = await process.stdout.readline()
        match = re.search(rb"127\.0\.0\.1:(\d+)", line)
        if match is None:
            raise RuntimeError("Scripted model port-forward did not announce its listener")
        model = ScriptedModel(f"http://127.0.0.1:{int(match[1])}")
        yield model
    finally:
        if model is not None:
            await model.aclose()
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        for resource in reversed(created):
            k8s.kubectl("delete", resource, "--wait=true", "--timeout=60s")


async def retire(api: Workspace, identifier: str) -> None:
    path = f"/api/v1/environments/{identifier}"
    current = await api.client.get(path)
    row = expect(current, 200)
    if row["status"] not in {"deleted", "deleting"}:
        expect(await api.client.delete(path, headers={"if-match": current.headers["etag"]}), 202)

    async def deleted():
        return expect(await api.client.get(path), 200)["status"] == "deleted"

    await eventually(deleted, timeout=120)


async def journey(api: Workspace, model: ScriptedModel, image: str) -> None:
    provider = expect(
        await api.client.post(
            "/api/v1/environment-providers",
            json={
                "type": "kubernetes",
                "name": "In-cluster",
                "config": {"cluster": "in-cluster", "namespace": k8s.NAMESPACE},
            },
        ),
        201,
    )
    recipe = {
        "execution_container": "envd",
        "connection": "direct",
        "pod_template": {
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {"name": "envd", "image": image, "imagePullPolicy": "Never"},
                ],
            }
        },
    }
    template = expect(
        await api.client.post(
            "/api/v1/environment-templates",
            json={
                "name": "Native sandbox",
                "provider_id": provider["id"],
                "config": {"recipe": recipe},
            },
        ),
        201,
    )
    agent = await api.create_agent("Deployment smoke", await api.create_model(MODEL_URL))
    await model.call("shell_exec", {"command": "echo cluster-direct-$((6 * 7)) > proof.txt"}, call_id="call_write")
    await model.call("view", {"file_path": "/workspace/proof.txt"}, call_id="call_read")
    await model.say("Verified direct Sandbox execution.")
    receipt = await api.start(
        agent,
        "Write and read the proof",
        options={
            "environment": {"mode": "fresh", "template_id": template["id"]},
            "execution": {"tools": {"mode": "inline", "foreground_wait_seconds": 60}},
        },
    )
    run_id = receipt["run"]["id"]
    identifier = receipt["run"]["environment_mounts"][0]["environment_id"]
    path = f"/api/v1/environments/{identifier}/kubernetes"
    try:
        run = await api.sealed(run_id, timeout=180)
        assert run["status"] == "completed", run["failure"]
        requests = await model.requests()
        assert len(requests) == 3
        assert "cluster-direct-42" in tool_results(requests[-1])[-1]
        native = expect(await api.client.get(f"{path}/diagnostics"), 200)["native"]
        assert native["connection"] == "direct" and native["envd"] == "previously_ready"
        references = [item["reference"] for item in native["resources"] if item["reference"]["disposition"] == "delete"]
        assert {ref["kind"] for ref in references} == {"Pod", "Secret"}
        pod = json.loads(k8s.kubectl("get", "pod", native["pod_name"], "-o", "json", capture=True))
        assert pod["metadata"]["uid"] == native["pod_uid"]
        assert pod["spec"]["serviceAccountName"] == "a13n-a13n-sandbox"
        assert pod["spec"]["automountServiceAccountToken"] is False
        assert pod["spec"]["containers"][0]["image"] == image
        logs = expect(await api.client.get(f"{path}/logs", params={"container": "envd", "tail_lines": 50}), 200)
        assert logs["pod_uid"] == native["pod_uid"] and logs["text"]
        await retire(api, identifier)
        for reference in references:
            assert not k8s.kubectl(
                "get", reference["kind"], reference["name"], "--ignore-not-found", "-o", "name", capture=True
            ).strip()
        print(
            f"{identifier}: deployed Worker executed shell + file read over direct Envd; diagnostics, logs and native retirement verified",
            flush=True,
        )
    except Exception:
        diagnostics = await api.client.get(f"{path}/diagnostics")
        print("Native diagnostics:", diagnostics.json().get("native", diagnostics.status_code), flush=True)
        raise
    finally:
        run = await api.run(run_id)
        if run["status"] in {"queued", "running"}:
            await api.interrupt(run_id)
            await api.sealed(run_id, timeout=60)
        await retire(api, identifier)


async def main() -> None:
    deployment = json.loads((k8s.state_directory() / "deployment.json").read_text())
    if not deployment["sandbox_image"]:
        raise RuntimeError("Run K8S_ENVIRONMENTS=1 make k8s-up first")
    admin = k8s.read_env(k8s.state_directory() / k8s.ADMIN_FILE)
    async with httpx2.AsyncClient(
        base_url=k8s.CONSOLE_URL, headers={"Origin": k8s.CONSOLE_URL}, trust_env=False, timeout=30
    ) as client:
        login = expect(
            await client.post("/api/v1/auth/login", json={"email": admin["EMAIL"], "password": admin["PASSWORD"]}), 200
        )
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        organization = expect(await client.get("/api/v1/organizations"), 200)["items"][0]["id"]
        workspace = expect(
            await client.post(
                f"/api/v1/organizations/{organization}/workspaces", json={"name": "Kubernetes deployment smoke"}
            ),
            201,
        )
        client.headers["X-Workspace-ID"] = workspace["id"]
        api = Workspace(client, {"organization_id": organization, "workspace_id": workspace["id"]})
        async with scripted_model(deployment["service_image"]) as model:
            await journey(api, model, deployment["sandbox_image"])


if __name__ == "__main__":
    asyncio.run(main())
