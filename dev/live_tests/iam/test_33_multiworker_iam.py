"""Case 33: native credentials and persisted User authority after multi-worker revocation."""

import asyncio
import secrets
import signal
from uuid import uuid4

import httpx2
import pytest

from ..infrastructure.client import agent_input
from ..infrastructure.management_packages import upload
from .native_iam import ORIGIN, native_clients

pytestmark = pytest.mark.anyio


async def test_multiworker_native_iam_revocation_and_recovery(multiworker):
    journey = multiworker
    live, lab = journey.live, journey.lab
    async with native_clients(lab) as (native, user):
        invitation = await native.post(
            journey.base + "/invitations", {"email": "authority-" + uuid4().hex + "@example.com", "role": "runner"}
        )
        assert invitation["delivery"] == "manual"
        accepted = await user.post(
            "/api/v1/invitations/" + invitation["invitation"]["id"] + "/accept",
            headers={"Origin": ORIGIN},
            json={"token": invitation["invitation_url"].split("#token=")[1], "password": secrets.token_urlsafe(30)},
        )
        assert accepted.status_code == 200, ("accept", accepted.status_code)
        user_id = accepted.json()["user"]["id"]
        # Explicit cookie transport on isolated loopback; native validation/CSRF remain enabled.
        user.headers.update(
            {
                "Cookie": "a13n_session=" + accepted.cookies.get("a13n_session"),
                "X-A13N-CSRF-Token": accepted.json()["csrf_token"],
                "Origin": ORIGIN,
                "X-A13N-Workspace-ID": live.config["workspace_id"],
            }
        )
        assert (await user.get("/api/v1/users/me")).status_code == 200
        key = await user.post(journey.base + "/personal-api-keys", json={"name": "revocation-e2e"})
        assert key.status_code == 201
        key_data = key.json()
        bindings = await native.live.collection(journey.base + "/role-bindings")
        binding = next(item for item in bindings if item["principal_id"] == user_id)

        async def submit(case, agent_id=None):
            response = await user.post(
                journey.base + "/runs",
                headers={"Idempotency-Key": uuid4().hex},
                json={**live.start_body(case), "agent_id": agent_id or live.config["agent_id"]},
            )
            assert response.status_code == 202, ("user run", response.status_code)
            receipt = response.json()
            live.track(receipt)
            return receipt

        approval = await journey.agent(
            plugins=[
                {
                    "instance_name": "approval",
                    "plugin_key": "live.approval",
                    "config": {"root": lab.config["workspace_root"]},
                }
            ]
        )
        approval_case = await live.case("approval")
        approval_receipt = await submit(approval_case, approval["agent"]["id"])
        waiting = await live.finish(approval_receipt["run_id"], "waiting")
        pending = await live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
        thread = await live.thread(waiting["thread_id"])
        feedback = {
            "expected_thread_version": thread["version"],
            "sealed_state_digest_sha256": waiting["sealed_state_digest_sha256"],
            "resolutions": [{"call_id": pending["items"][0]["call_id"], "action": "approve"}],
        }

        # Occupy all three slots as the test User; retain real initial process ownership.
        cases = [await live.case("gate") for _ in range(3)]
        receipts = [await submit(case) for case in cases]
        for case, receipt in zip(cases, receipts, strict=True):
            await lab.wait_evidence(case, "gate_ready", run_id=receipt["run_id"])
        owners = [lab.execution_owner(receipt["run_id"]) for receipt in receipts]
        assert len({worker.pid for worker in owners}) == 3
        run = await live.run(receipts[0]["run_id"])
        run_thread = await live.thread(run["thread_id"])
        asset = await upload(
            journey,
            "assets",
            b"AUTHORITY_PRIVATE_ASSET\n",
            params={"filename": "authority.txt", "media_type": "text/plain"},
        )
        paths = [
            f"/api/v1/runs/{run['id']}",
            f"/api/v1/runs/{waiting['id']}/items",
            f"/api/v1/runs/{run['id']}/attempts",
            f"/api/v1/threads/{run['thread_id']}",
            f"/api/v1/sessions/{run['session_id']}/threads",
            f"/api/v1/assets/{asset['id']}",
            f"/api/v1/assets/{asset['id']}/content",
        ]
        for path in paths:
            response = await user.get(path)
            assert response.status_code == 200, ("before revocation", path, response.status_code)
        async with httpx2.AsyncClient(
            base_url=str(user.base_url),
            headers={"Authorization": "Bearer " + key_data["bearer"]},
            trust_env=False,
            timeout=15,
        ) as keyed:
            assert (await keyed.get(paths[0])).status_code == 200
            stream_ready = asyncio.Event()
            stream_events = []

            async def consume():
                async with user.stream(
                    "GET", f"/api/v1/runs/{run['id']}/stream", headers={"Accept": "text/event-stream"}, timeout=45
                ) as response:
                    assert response.status_code == 200
                    async for line in response.aiter_lines():
                        stream_events.append(line)
                        if line.startswith("id:"):
                            stream_ready.set()

            stream_task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(stream_ready.wait(), 20)
                current = await native.live.http.get("/api/v1/role-bindings/" + binding["id"])
                removed = await native.live.http.delete(
                    "/api/v1/role-bindings/" + binding["id"], headers={"If-Match": current.headers["etag"]}
                )
                assert removed.status_code == 204
                for path in paths:
                    response = await user.get(path)
                    assert response.status_code in {403, 404}, ("revoked read", path, response.status_code)
                    assert b"AUTHORITY_PRIVATE_ASSET" not in response.content
                assert (await user.get("/api/v1/users/me")).status_code == 200
                assert (await keyed.get(paths[0])).status_code == 401
                for action, body in {
                    "interrupt": {
                        "expected_run_version": run["version"],
                        "expected_thread_version": run_thread["version"],
                    },
                    "steer": agent_input("unauthorized"),
                    "fork": {"input": agent_input("unauthorized")},
                    "continue": {
                        "expected_thread_version": run_thread["version"],
                        "input": agent_input("unauthorized"),
                    },
                }.items():
                    response = await user.post(
                        f"/api/v1/runs/{run['id']}/{action}", headers={"Idempotency-Key": uuid4().hex}, json=body
                    )
                    assert response.status_code in {403, 404}, (action, response.status_code)
                response = await user.post(
                    f"/api/v1/runs/{waiting['id']}/feedback", headers={"Idempotency-Key": uuid4().hex}, json=feedback
                )
                assert response.status_code in {403, 404}
                assert (await live.evidence(approval_case)).get("approval_executions", 0) == 0
                await asyncio.wait_for(stream_task, 40)
                assert not any("run.completed" in item for item in stream_events)
                async with user.stream(
                    "GET",
                    f"/api/v1/runs/{run['id']}/stream",
                    headers={
                        "Accept": "text/event-stream",
                        "Last-Event-ID": next(item[3:].strip() for item in stream_events if item.startswith("id:")),
                    },
                ) as denied_stream:
                    assert denied_stream.status_code in {403, 404}, ("stream reconnect", denied_stream.status_code)

            finally:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)

            # All old attempts die; replacements must recheck the persisted User principal.
            for worker in owners:
                await lab.stop(worker, signal.SIGKILL)
            for _ in range(3):
                await lab.start_worker()
            for case in cases:
                await live.release(case)
            for case, receipt in zip(cases, receipts, strict=True):
                await live.finish(receipt["run_id"], "failed")
                attempts = await lab.attempts(receipt["run_id"])
                assert len(attempts) == 2 and attempts[-1]["status"] == "failed"
                assert (await live.evidence(case))["model_requests"] == 1

            await native.post(journey.base + "/role-bindings", {"principal_id": user_id, "role": "runner"})
            assert (await user.get(paths[0])).status_code == 200
            assert (await keyed.get(paths[0])).status_code == 401

        # Same user's valid cookie cannot turn a foreign resource ID into authority.
        other = live.config["other_identity"]
        for path in (
            f"/api/v1/workspaces/{other['workspace_id']}/sessions",
            f"/api/v1/workspaces/{other['workspace_id']}",
        ):
            response = await user.get(path)
            assert response.status_code in {403, 404}
        assert (await user.post("/api/v1/auth/logout")).status_code == 204
        assert (await user.get("/api/v1/users/me")).status_code == 401
