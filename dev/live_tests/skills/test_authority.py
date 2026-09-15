"""Native IAM role boundaries, upload ownership, and current-authority rechecks."""

import asyncio
import secrets
from uuid import uuid4

import pytest

from ..iam.native_iam import ORIGIN, native_clients
from ..infrastructure.client import LiveClient
from ..infrastructure.management_packages import skill_zip, upload
from ..infrastructure.round_two_resources import agent_config, provision
from .support import SkillJourney

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
async def skill_native(skill_lab):
    async with native_clients(skill_lab) as clients:
        yield clients


async def member(native, user, role):
    invitation = await native.post(
        native.base + "/invitations", {"email": "skill-" + uuid4().hex + "@example.com", "role": role}
    )
    user.cookies.clear()
    user.headers.clear()
    accepted = await user.post(
        "/api/v1/invitations/" + invitation["invitation"]["id"] + "/accept",
        headers={"Origin": ORIGIN},
        json={"token": invitation["invitation_url"].split("#token=")[1], "password": secrets.token_urlsafe(30)},
    )
    assert accepted.status_code == 200
    user.headers.update(
        {
            "Cookie": "a13n_session=" + accepted.cookies.get("a13n_session"),
            "X-A13N-CSRF-Token": accepted.json()["csrf_token"],
            "Origin": ORIGIN,
            "X-A13N-Workspace-ID": native.live.config["workspace_id"],
        }
    )
    assert (await user.get("/api/v1/users/me")).status_code == 200
    return next(
        item
        for item in await native.live.collection(native.base + "/role-bindings")
        if item["principal_id"] == accepted.json()["user"]["id"]
    )


@pytest.mark.parametrize("role", ["viewer", "runner", "builder", "admin"])
async def test_skill_native_workspace_role_matrix(skills: SkillJourney, skill_native, role):
    native, user = skill_native
    await member(native, user, role)
    first = await skills.skill()
    path = "/api/v1/skills/" + first["skill"]["id"]
    revision_path = "/api/v1/skill-revisions/" + first["revision"]["id"]
    for target in (path, path + "/revisions", revision_path, revision_path + "/content", path + "/references"):
        assert (await user.get(target)).status_code == 200, (role, target)
    archive = skill_zip("authority-" + uuid4().hex, "DOCUMENT", "ATTACHMENT")
    staged = await user.post(
        skills.base + "/skill-uploads",
        content=archive,
        headers={"Content-Type": "application/zip", "Idempotency-Key": uuid4().hex},
    )
    head = await user.get(path)
    renamed = await user.patch(path, headers={"If-Match": head.headers["etag"]}, json={"name": "Renamed Skill"})
    if role in {"viewer", "runner"}:
        assert staged.status_code in {403, 404}
        assert renamed.status_code in {403, 404}
        unchanged = await skills.live.http.get(path)
        assert unchanged.json()["name"] == first["skill"]["name"]
    else:
        assert staged.status_code == 201
        created = await user.post(
            skills.base + "/skills",
            headers={"Idempotency-Key": uuid4().hex},
            json={"source": {"kind": "zip_upload", "upload_id": staged.json()["upload_id"]}},
        )
        assert created.status_code == 201
        assert renamed.status_code == 200 and renamed.json()["version"] == 1


async def test_skill_upload_is_private_to_uploading_principal(skills: SkillJourney, skill_native):
    native, user = skill_native
    await member(native, user, "builder")
    key = "owner-" + uuid4().hex
    staged = await upload(
        skills, "skill-uploads", skill_zip(key, "DOCUMENT", "ATTACHMENT"), content_type="application/zip"
    )
    path = "/api/v1/skill-uploads/" + staged["upload_id"]
    assert (await user.get(skills.base + "/skills")).status_code == 200
    for method in ("GET", "DELETE"):
        denied = await user.request(method, path)
        assert denied.status_code in {403, 404}
    body = {"source": {"kind": "zip_upload", "upload_id": staged["upload_id"]}}
    denied = await user.post(skills.base + "/skills", json=body, headers={"Idempotency-Key": uuid4().hex})
    assert denied.status_code in {403, 404}
    created = await skills.post(skills.base + "/skills", body)
    assert created["skill"]["key"] == key


async def test_skill_cross_workspace_management_and_binding_are_denied(skills: SkillJourney):
    first = await skills.skill()
    staged = await upload(
        skills, "skill-uploads", skill_zip("other-" + uuid4().hex, "PRIVATE", "PRIVATE"), content_type="application/zip"
    )
    path = "/api/v1/skills/" + first["skill"]["id"]
    head = await skills.live.http.get(path)
    async with skills.outsider() as other:
        other_base = "/api/v1/workspaces/" + skills.live.config["other_identity"]["workspace_id"]
        requests = [
            ("GET", skills.base + "/skills", {}),
            ("GET", path + "/references", {}),
            ("GET", "/api/v1/skill-uploads/" + staged["upload_id"], {}),
            ("DELETE", "/api/v1/skill-uploads/" + staged["upload_id"], {}),
            ("PATCH", path, {"json": {"name": "Unauthorized"}}),
            ("DELETE", path, {}),
            (
                "POST",
                path + "/revisions",
                {"json": {"expected_version": 1, "source": {"kind": "zip_upload", "upload_id": staged["upload_id"]}}},
            ),
            (
                "POST",
                other_base + "/skills",
                {"json": {"source": {"kind": "zip_upload", "upload_id": staged["upload_id"]}}},
            ),
        ]
        for method, target, options in requests:
            denied = await other.request(
                method, target, headers={"If-Match": head.headers["etag"], "Idempotency-Key": uuid4().hex}, **options
            )
            assert denied.status_code in {403, 404}, (method, target, denied.status_code)
            assert "DOCUMENT_ONE" not in denied.text and "ATTACHMENT_ONE" not in denied.text
        # Establish working Model/Agent creation in the other Workspace first, so
        # a missing Model cannot masquerade as denied foreign Skill resolution.
        other_config = {**skills.live.config, **skills.live.config["other_identity"]}
        await provision(LiveClient(other_config, other))
        # Create in the accessible workspace with a foreign key: no cross-workspace resolution.
        denied = await other.post(
            other_base + "/agents",
            headers={"Idempotency-Key": uuid4().hex},
            json={"name": "Foreign binding", "config": agent_config(skills=[{"skill_key": first["skill"]["key"]}])},
        )
        assert denied.status_code == 409, (denied.status_code, denied.json())
        assert denied.json()["error"]["details"]["reason"] == "skill_selection_invalid"
    assert (await skills.live.http.get(path)).json() == head.json()
    assert (await skills.live.http.get("/api/v1/skill-uploads/" + staged["upload_id"])).status_code == 200


@pytest.mark.parametrize("operation", ["create", "revision", "binding", "run"])
async def test_skill_reauthorizes_after_preparation(skills: SkillJourney, skill_native, operation):
    native, user = skill_native
    binding = await member(native, user, "builder")
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}])
    case = await skills.case()
    if operation in {"create", "revision"}:
        archive = skill_zip("revoke-" + uuid4().hex if operation == "create" else key, "DOCUMENT_TWO", "ATTACHMENT_TWO")
        staged = await user.post(
            skills.base + "/skill-uploads",
            content=archive,
            headers={"Content-Type": "application/zip", "Idempotency-Key": uuid4().hex},
        )
        assert staged.status_code == 201
        body = {"source": {"kind": "zip_upload", "upload_id": staged.json()["upload_id"]}}
        path = (
            skills.base + "/skills"
            if operation == "create"
            else "/api/v1/skills/" + first["skill"]["id"] + "/revisions"
        )
        if operation == "revision":
            body["expected_version"] = 1
        barrier = skills.arm("skill.source_prepared", upload_id=staged.json()["upload_id"])
    elif operation == "binding":
        path = skills.base + "/agents/" + agent["agent"]["id"] + "/revisions"
        body = {"expected_version": 1, "config": agent_config(skills=[{"skill_key": key, "version": 1}])}
        barrier = skills.arm("skill.binding_prepared", agent_id=agent["agent"]["id"])
    else:
        environment, _ = await skills.environment()
        path = skills.base + "/runs"
        body = {
            **skills.live.start_body(case),
            "agent_id": agent["agent"]["id"],
            "environment": {"environment_id": environment["id"]},
        }
        barrier = skills.arm("skill.invocation_prepared", agent_id=agent["agent"]["id"])
    before = await skills.runs()
    task = asyncio.create_task(user.post(path, json=body, headers={"Idempotency-Key": uuid4().hex}))
    try:
        await skills.reached(barrier)
        binding_path = "/api/v1/role-bindings/" + binding["id"]
        current = await native.live.http.get(binding_path)
        removed = await native.live.http.delete(binding_path, headers={"If-Match": current.headers["etag"]})
        assert removed.status_code == 204
        skills.release(barrier)
        response = await task
        if response.status_code == 202:
            skills.live.track(response.json())
        assert response.status_code in {403, 404}, (operation, response.status_code, response.json())
        assert await skills.runs() == before
        assert skills.observations(case) == []
        assert (await skills.live.http.get("/api/v1/skills/" + first["skill"]["id"])).json()["version"] == 1
        assert (await skills.live.http.get(skills.base + "/agents/" + agent["agent"]["id"])).json()["version"] == 1
    finally:
        skills.release(barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
