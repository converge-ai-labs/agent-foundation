"""Native IAM sessions and real PostgreSQL govern shared Model configuration."""

import asyncio
import secrets
from uuid import uuid4

import httpx2
import pytest

from ..iam.native_iam import ORIGIN, native_clients
from ..infrastructure.client import agent_input

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
async def native(model_lab):
    async with native_clients(model_lab.lab) as (admin, _unused):
        yield admin


@pytest.fixture(scope="module")
async def organization(native):
    headers = dict(native.live.http.headers)
    headers.pop("x-a13n-workspace-id")
    async with httpx2.AsyncClient(
        base_url=native.live.http.base_url, headers=headers, trust_env=False, timeout=45
    ) as client:
        yield client


async def shared_model(journey, organization):
    case = await journey.model()
    base = "/api/v1/organizations/" + journey.live.config["organization_id"]
    response = await organization.post(
        base + "/model-providers",
        json={
            "name": "Shared " + uuid4().hex,
            "type": "openai",
            "credential": "fixture-primary",
            "configuration": case["provider"]["configuration"],
        },
    )
    assert response.status_code == 201
    provider = response.json()
    response = await organization.post(
        base + "/models",
        json={
            "key": "shared-" + uuid4().hex,
            "name": "Shared model",
            "provider_id": provider["id"],
            "upstream_model": "manual-model",
            "model_api": "openai.chat_completions",
            "settings": {"temperature": 0.3},
        },
    )
    assert response.status_code == 201
    case.update(model=response.json(), provider=provider)
    return case


async def test_organization_model_visible_and_executable_in_both_workspaces(model_lab, native, organization):
    journey = model_lab
    case = await shared_model(journey, organization)
    assert case["model"]["workspace_id"] is None
    model_path = journey.base + "/models/" + case["model"]["id"]
    assert (await native.live.request("GET", model_path))["key"] == case["model"]["key"]
    local = await native.live.http.get(journey.base + "/models", params={"scope": "workspace"})
    assert case["model"]["id"] not in {item["id"] for item in local.json()["items"]}
    shared = await native.live.http.get(journey.base + "/models", params={"scope": "organization"})
    assert case["model"]["id"] in {item["id"] for item in shared.json()["items"]}
    assert (await journey.invoke(case))["output_text"] == case["answer"]
    async with journey.outsider() as other:
        base = "/api/v1/workspaces/" + journey.live.config["other_identity"]["workspace_id"]
        assert (await other.get(base + "/models/" + case["model"]["id"])).status_code == 200
        from ..infrastructure.round_two_resources import agent_config

        agent = await other.post(
            base + "/agents",
            headers={"Idempotency-Key": uuid4().hex},
            json={"name": "Shared model consumer", "config": agent_config(case["model"]["key"])},
        )
        assert agent.status_code == 201
        run = await other.post(
            base + "/runs",
            headers={"Idempotency-Key": uuid4().hex},
            json={"agent_id": agent.json()["agent"]["id"], "input": agent_input("Shared proof")},
        )
        assert run.status_code == 202
        run_id = run.json()["run_id"]

        async def get_run():
            response = await other.get("/api/v1/runs/" + run_id)
            assert response.status_code == 200
            return response.json()

        try:
            done = await journey.live.wait(
                get_run, lambda value: value["status"] in {"completed", "failed"}, "sibling Workspace model execution"
            )
            assert done["status"] == "completed" and done["output_text"] == case["answer"]
            assert (await journey.live.http.get("/api/v1/runs/" + run_id)).status_code == 404
        finally:
            await other.post(f"/api/v1/runs/{run_id}/interrupt")


@pytest.mark.parametrize("role", ["viewer", "builder"])
async def test_native_workspace_role_cannot_mutate_shared_configuration(model_lab, native, organization, role):
    journey = model_lab
    case = await shared_model(journey, organization)
    invitation = await native.post(journey.base + "/invitations", {"email": uuid4().hex + "@example.com", "role": role})
    async with httpx2.AsyncClient(base_url=native.live.http.base_url, trust_env=False, timeout=45) as user:
        accepted = await user.post(
            f"/api/v1/invitations/{invitation['invitation']['id']}/accept",
            headers={"Origin": ORIGIN},
            json={
                "token": invitation["invitation_url"].split("#token=")[1],
                "password": secrets.token_urlsafe(30),
            },
        )
        assert accepted.status_code == 200
        user.headers.update(
            {
                "Cookie": "a13n_session=" + accepted.cookies.get("a13n_session"),
                "X-A13N-CSRF-Token": accepted.json()["csrf_token"],
                "Origin": ORIGIN,
                "X-A13N-Workspace-ID": journey.live.config["workspace_id"],
            }
        )
        provider_path = journey.base + "/model-providers/" + case["provider"]["id"]
        model_path = journey.base + "/models/" + case["model"]["id"]
        for path in [provider_path, model_path]:
            before = await user.get(path)
            assert before.status_code == 200
            changed = await user.patch(path, json={"enabled": False}, headers={"If-Match": before.headers["etag"]})
            # Shared writes through a Workspace route conceal the Organization owner.
            assert changed.status_code == 404
            assert (await user.get(path)).json() == before.json()
        for path in [provider_path + "/test", model_path + "/test"]:
            result = await user.post(path, json={})
            assert result.status_code == (404 if role == "viewer" else 200)
        assert (
            await user.get("/api/v1/workspaces/" + journey.live.config["other_identity"]["workspace_id"] + "/models")
        ).status_code == 404
        user.headers.pop("X-A13N-Workspace-ID")
        changed = await user.patch(
            f"/api/v1/organizations/{journey.live.config['organization_id']}/models/{case['model']['id']}",
            json={"enabled": False},
            headers={"If-Match": before.headers["etag"]},
        )
        # A Workspace-only principal has no Organization boundary.
        assert changed.status_code == 404


async def test_org_workspace_key_race_and_foreign_scope_are_concealed(model_lab, native, organization):
    journey = model_lab
    case = await shared_model(journey, organization)
    body = {key: case["model"][key] for key in ["provider_id", "name", "upstream_model", "model_api"]}
    body["key"] = "race-" + uuid4().hex
    responses = await asyncio.gather(
        organization.post(f"/api/v1/organizations/{journey.live.config['organization_id']}/models", json=body),
        native.live.http.post(journey.base + "/models", json=body),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()["error"]["code"]
        == "model_key_conflict"
    )
    assert (
        await organization.get("/api/v1/organizations/org_foreign/models/" + case["model"]["id"])
    ).status_code == 404
