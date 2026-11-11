"""Real Linux injection and rotation through Worker HTTP and Control reverse WebSocket ownership."""

import asyncio
import hashlib
import json
from uuid import uuid4

import pytest

from .api import eventually, expect
from .egress import guest
from .egress_guest import ALLOWED, OTHER
from .scripted import tool_results

pytestmark = [pytest.mark.anyio, pytest.mark.isolated_service(host="0.0.0.0")]


def digest(value: str) -> str:
    return hashlib.sha256(f"Bearer {value}".encode()).hexdigest()


async def application(api, path: str, **expected):
    async def check():
        items = expect(await api.client.get(path + "/sessions"), 200)["items"]
        return next((row for row in items if all(row[key] == value for key, value in expected.items())), None)

    return await eventually(check)


@pytest.mark.parametrize("carrier", ["http", "reverse_websocket"])
async def test_docker_credential_rotation_and_idle_attach(stack, request, carrier):
    api, model = stack.api, stack.model
    device_id = "device-" + uuid4().hex
    token = uuid4().hex
    reverse = {}
    environment_id = None
    if carrier == "reverse_websocket":
        registered = expect(
            await api.client.post(
                "/api/v1/client-environments",
                json={"device_id": device_id, "name": "Egress client", "default_working_directory": "/workspace"},
            ),
            201,
        )
        environment_id = registered["client"]["environment"]["id"]
        token = registered["token"]
        reverse = {
            "reverse_url": stack.control.url.replace("https://", "wss://") + registered["connection_path"],
            "control_port": stack.control.port,
            "control_certificate": stack.stores.certificate,
        }
    async with guest(stack.directory / "guest", request.config, device_id=device_id, token=token, **reverse) as native:
        if environment_id is None:
            registered = expect(
                await api.client.post("/api/v1/environments", json={"endpoint": native.endpoint, "token": token}), 201
            )
            environment_id = registered["id"]
        else:

            async def connected():
                current = expect(await api.client.get(f"/api/v1/client-environments/{environment_id}"), 200)
                return current["environment"]["client_connection"]["state"] == "online"

            await eventually(connected)

        finish = native.workspace / "control" / "finish"
        values = ["fixture-" + uuid4().hex for _ in range(4)]
        response = await api.client.post(
            "/api/v1/egress-credentials", json={"name": "Journey", "allowed_hosts": [ALLOWED], "value": values[0]}
        )
        credential = expect(response, 201)
        path, etag = f"/api/v1/egress-credentials/{credential['id']}", response.headers["etag"]
        agent = await api.create_agent(
            "Egress",
            await api.create_model(model.base_url),
            default_environment_id=environment_id,
            default_session_configuration={
                "egress_mode": "controlled",
                "egress": {
                    "destinations": {"mode": "allowlist", "hosts": [ALLOWED, OTHER]},
                    "secrets": [{"env": "GH_TOKEN", "credential_id": credential["id"], "inject_hosts": [ALLOWED]}],
                },
            },
        )
        command = (
            "cd /workspace || exit 8; "
            'printf "%s" "$GH_TOKEN" | sha256sum > marker; '
            f'curl --fail --max-time 5 -sS https://{OTHER}/denied -H "Authorization: Bearer $GH_TOKEN"; '
            "test $? -ne 0 || exit 9; "
            "while [ ! -f control/finish ]; do "
            f'curl --fail --max-time 5 -sS https://{ALLOWED}/live -H "Authorization: Bearer $GH_TOKEN" || exit 10; '
            "sleep 0.2; done"
        )
        await model.call("shell_exec", {"command": command}, call_id="call_live", to="[live]")
        await model.say("Background command started.", to="[live]")
        receipt = await api.start(agent, "[live] Start authenticated requests")
        thread_id, run_id = receipt["thread"]["id"], receipt["run"]["id"]

        async def received(value: str, route: str):
            async def check():
                return any(row["digest"] == digest(value) and row["path"] == route for row in native.requests())

            try:
                await eventually(check)
            except TimeoutError as error:
                error.add_note("Tool results: " + json.dumps([tool_results(entry) for entry in await model.requests()]))
                raise

        try:
            await received(values[0], "/live")
            assert (await api.sealed(run_id))["status"] == "completed"
            first = await application(api, path, status="applied", execution_active=True)
            marker = (native.workspace / "marker").read_text()
            assert all(hashlib.sha256(value.encode()).hexdigest() not in marker for value in values)
            assert not any(row["path"] == "/denied" for row in native.requests())

            rotated = await api.client.put(path + "/value", json={"value": values[1]}, headers={"if-match": etag})
            assert expect(rotated, 202)["credential_version"] == 2
            etag = rotated.headers["etag"]
            await received(values[1], "/live")
            live = await application(api, path, status="applied", applied_credential_version=2, execution_active=True)
            assert live["native_session_id"] == first["native_session_id"]
            assert (native.workspace / "marker").read_text() == marker
            await model.say("Background command finished.", to="[live]")
        finally:
            finish.touch()

        completion = await api.next_run(thread_id, run_id)
        assert (await api.sealed(completion["id"]))["status"] == "completed"
        await application(api, path, execution_active=False)
        for value in values[2:]:
            rotated = await api.client.put(path + "/value", json={"value": value}, headers={"if-match": etag})
            expect(rotated, 202)
            etag = rotated.headers["etag"]
        idle = await application(api, path, status="on_next_use", applied_credential_version=2)
        assert idle["native_session_id"] == first["native_session_id"]
        # Deliberately cross the 10-second compensation interval: an idle binding must stay idle.
        await asyncio.sleep(11)
        idle = await application(api, path, status="on_next_use", applied_credential_version=2, execution_active=False)
        assert idle["native_session_id"] == first["native_session_id"]

        finish.unlink()
        await model.call(
            "shell_exec",
            {
                "command": 'cd /workspace || exit 8; printf "%s" "$GH_TOKEN" | sha256sum > marker-after; '
                "while [ ! -f control/finish ]; do "
                f'curl --fail --max-time 5 -sS https://{ALLOWED}/reattach -H "Authorization: Bearer $GH_TOKEN" '
                "|| { touch revoked; exit 0; }; sleep 0.2; done"
            },
            call_id="call_reattach",
            to="[reattach]",
        )
        await model.say("Authenticated.", to="[reattach]")
        try:
            resumed = await api.send(thread_id, agent, "[reattach] Authenticate again")
            assert (await api.sealed(resumed["run"]["id"]))["status"] == "completed"
            await received(values[3], "/reattach")
            latest = await application(api, path, status="applied", applied_credential_version=4, execution_active=True)
            assert latest["native_session_id"] == first["native_session_id"]
            assert (native.workspace / "marker-after").read_text() == marker

            revoked = await api.client.post(path + "/revoke", headers={"if-match": etag})
            assert expect(revoked, 202)["credential_version"] == 5
            removed = await application(api, path, status="applied", applied_credential_version=5)
            assert removed["native_session_id"] == first["native_session_id"]

            async def refused():
                return (native.workspace / "revoked").exists()

            await eventually(refused)
        finally:
            finish.touch()

        rejected = await api.start(agent, "Use the revoked credential")
        assert rejected["run"] is None
        assert rejected["entry"]["status"] == "failed"
        assert rejected["entry"]["failure"] == {
            "code": "invalid_argument",
            "message": "egress.secrets.0: credential_revoked",
        }

        requests = await model.requests()
        encoded = json.dumps(requests)
        for entry in requests:
            instructions = json.dumps(
                [part for part in entry["body"]["messages"] if part["role"] in {"system", "developer"}]
            )
            assert instructions.count("Configured HTTPS credentials for this Run:") == 1
            assert "GH_TOKEN" in instructions and ALLOWED in instructions
        assert all(value not in encoded for value in values)
        logs = "\n".join(process.log.read_text() for process in [stack.control, *stack.workers])
        assert all(value not in logs for value in values)
