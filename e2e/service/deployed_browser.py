"""Exercise the Kubernetes Browser through Helm-deployed Control, Worker and Chromium."""

import asyncio
import hashlib
import json
from urllib.parse import urlsplit

import httpx2
from websockets.asyncio.client import connect
from websockets.typing import Origin, Subprotocol

from scripts import k8s_local as k8s

from .api import Workspace, eventually, expect
from .deployed_kubernetes import MODEL_URL, scripted_model
from .scripted import ScriptedModel, tool_results


async def journey(api: Workspace, model: ScriptedModel, namespace: str) -> None:
    client = api.client
    types = expect(await client.get("/api/v1/provider-types/browser"), 200)
    assert any(item["type"] == "kubernetes" for item in types["items"])
    provider = expect(
        await client.post(
            "/api/v1/browser-providers",
            json={
                "type": "kubernetes",
                "name": "In-cluster Browser",
                "config": {"cluster": "in-cluster", "namespace": namespace},
            },
        ),
        201,
    )
    browser = expect(
        await client.post("/api/v1/browsers", json={"provider_id": provider["id"], "name": "Chrome smoke"}), 201
    )
    path = f"/api/v1/browsers/{browser['id']}"
    name = "a13n-browser-" + hashlib.sha256(browser["id"].encode()).hexdigest()[:32]

    def native(resource: str, identifier: str) -> dict | None:
        raw = k8s.kubectl(
            "-n", namespace, "get", resource, identifier, "--ignore-not-found", "-o", "json", capture=True
        )
        return json.loads(raw) if raw.strip() else None

    async def command(operation: str, body: dict | None = None) -> dict:
        current = await client.get(path)
        expect(current, 200)
        return expect(
            await client.post(
                f"{path}/{operation}", json=body, headers={"If-Match": current.headers["etag"]}, timeout=145
            ),
            200,
        )

    async def state(status: str) -> dict:
        async def check():
            row = expect(await client.get(path), 200)
            return row if row["status"] == status else None

        return await eventually(check, timeout=150)

    async def prepare() -> dict:
        # Explicitly drive the second phase even if the maintenance sweep hasn't run yet.
        async def activate():
            row = await command("prepare")
            return row if row["status"] == "ready" else None

        return await eventually(activate, timeout=150)

    failure: BaseException | None = None
    try:
        first = await prepare()
        claim_name = f"profile-{name}-0"
        pod = native("pod", name + "-0")
        claim = native("persistentvolumeclaim", claim_name)
        assert pod and claim and not pod["spec"]["automountServiceAccountToken"]
        uid, pvc_uid = pod["metadata"]["uid"], claim["metadata"]["uid"]
        agent = await api.create_agent(
            "Browser deployment smoke", await api.create_model(MODEL_URL), toolsets={"browser": {"enabled": True}}
        )
        await model.say("Ready to use Chromium.")
        initial = await api.start(agent, "Start a thread")
        thread_id = initial["thread"]["id"]
        initial_run = await api.sealed(initial["run"]["id"], timeout=90)
        assert initial_run["status"] == "completed", initial_run["failure"]
        thread = await client.get(f"/api/v1/threads/{thread_id}")
        expect(
            await client.put(
                f"/api/v1/threads/{thread_id}/browser",
                headers={"If-Match": thread.headers["etag"]},
                json={
                    "browser_id": browser["id"],
                    "preparation": "first_use",
                    "tab_access": "thread",
                },
            ),
            200,
        )
        await model.call(
            "browser_tabs",
            {"action": "new", "url": "data:text/html,<title>kind-chromium-proof</title><p>Deployed Chromium</p>"},
            call_id="call_open",
        )
        await model.call("browser_observe", {}, call_id="call_observe")
        await model.say("Verified deployed Chromium.")
        receipt = await api.send(thread_id, agent, "Check the Chromium page")
        run = await api.sealed(receipt["run"]["id"], timeout=100)
        assert run["status"] == "completed", run["failure"]
        assert "kind-chromium-proof" in tool_results((await model.requests())[-1])[-1]

        await command("takeover", {"thread_id": thread_id})
        grant = expect(await client.post(f"{path}/viewer"), 200)
        assert grant["protocol"] == "vnc" and grant["password"]
        viewer_path = urlsplit(grant["url"]).path
        assert viewer_path == path + "/viewer/connect"
        async with connect(
            k8s.CONSOLE_URL.replace("http:", "ws:") + viewer_path,
            origin=Origin(k8s.CONSOLE_URL),
            subprotocols=[Subprotocol("binary"), Subprotocol("a13n.browser." + grant["token"])],
            proxy=None,
        ) as socket:
            greeting = await asyncio.wait_for(socket.recv(), 15)
            assert isinstance(greeting, bytes) and greeting.startswith(b"RFB 003.")
            await command("handback")
            await asyncio.wait_for(socket.wait_closed(), 15)

        await command("stop")
        await state("stopped")
        assert native("pod", name + "-0") is None
        retained = native("persistentvolumeclaim", claim_name)
        assert retained and retained["metadata"]["uid"] == pvc_uid
        restarted = await prepare()
        assert restarted["generation"] > first["generation"]
        replacement = native("pod", name + "-0")
        assert replacement and replacement["metadata"]["uid"] != uid
        retained = native("persistentvolumeclaim", claim_name)
        assert retained and retained["metadata"]["uid"] == pvc_uid
        print(
            "Kubernetes Browser: deployed Worker read real Chromium; VNC relay closed on handback; stop/start retained the exact profile claim.",
            flush=True,
        )
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            current = expect(await client.get(path), 200)
            if current["control"] != "automation":
                await command("handback")
            if current["status"] not in {"deleted", "deleting"}:
                await command("release")
            await state("deleted")
            for kind, identifier in [
                ("pod", name + "-0"),
                ("persistentvolumeclaim", f"profile-{name}-0"),
                ("secret", name),
                ("statefulset", name),
            ]:
                assert native(kind, identifier) is None
            print("Kubernetes Browser: release reclaimed the Pod, claim, credential, and StatefulSet.", flush=True)
        except Exception as cleanup_error:
            if failure is None:
                raise
            failure.add_note(f"Browser cleanup also failed: {cleanup_error}")


async def main() -> None:
    deployment = json.loads((k8s.state_directory() / "deployment.json").read_text())
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
                f"/api/v1/organizations/{organization}/workspaces", json={"name": "Kubernetes Browser smoke"}
            ),
            201,
        )
        client.headers["X-Workspace-ID"] = workspace["id"]
        api = Workspace(client, {"organization_id": organization, "workspace_id": workspace["id"]})
        async with scripted_model(deployment["service_image"]) as model:
            await journey(api, model, deployment["browser_namespace"])


if __name__ == "__main__":
    asyncio.run(main())
