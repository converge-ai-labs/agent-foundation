"""Native HTTP authorization with fixture-seeded direct Agent RoleBindings."""

from uuid import uuid4

import pytest

from ..iam.native_iam import native_clients
from ..infrastructure.management_packages import publish_skill
from ..infrastructure.round_two_resources import agent_config
from .support import SkillJourney, proof
from .test_authority import member

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
async def skill_native(skill_lab):
    async with native_clients(skill_lab) as clients:
        yield clients


async def grant(skills, binding, agent, role):
    await skills.lab.command("dev.live_tests.skills.grant_fixture", binding["principal_id"], agent["agent"]["id"], role)


async def revoke_workspace(native, binding):
    path = "/api/v1/role-bindings/" + binding["id"]
    current = await native.live.http.get(path)
    assert current.status_code == 200
    removed = await native.live.http.delete(path, headers={"If-Match": current.headers["etag"]})
    assert removed.status_code == 204


async def test_agent_builder_binds_readable_skill_without_management(skills: SkillJourney, skill_native):
    native, user = skill_native
    binding = await member(native, user, "viewer")
    skill = await skills.skill()
    target, other = await skills.agent(), await skills.agent()
    await grant(skills, binding, target, "builder")
    body = {"expected_version": 1, "config": agent_config(skills=[{"skill_key": skill["skill"]["key"]}])}
    path = skills.base + "/agents/" + target["agent"]["id"]
    created = await user.post(path + "/revisions", json=body, headers={"Idempotency-Key": uuid4().hex})
    assert created.status_code == 201, created.json()
    foreign = await user.post(
        skills.base + "/agents/" + other["agent"]["id"] + "/revisions",
        json=body,
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert foreign.status_code in {403, 404}
    skill_path = "/api/v1/skills/" + skill["skill"]["id"]
    head = await user.get(skill_path)
    assert head.status_code == 200
    denied = await user.patch(skill_path, json={"name": "Forbidden"}, headers={"If-Match": head.headers["etag"]})
    assert denied.status_code in {403, 404}
    await revoke_workspace(native, binding)
    assert (await user.get(path)).status_code == 200
    assert (await user.get("/api/v1/skill-revisions/" + skill["revision"]["id"] + "/content")).status_code in {403, 404}
    denied = await user.post(
        path + "/revisions", json={**body, "expected_version": 2}, headers={"Idempotency-Key": uuid4().hex}
    )
    assert denied.status_code == 409, denied.json()
    assert denied.json()["error"]["code"] == "agent_revision_create_failed"
    assert denied.json()["error"]["details"]["reason"] == "managed_resource_unavailable"
    assert (await skills.live.http.get(path)).json()["version"] == 2
    assert (await skills.live.http.get(skill_path)).json() == head.json()


@pytest.mark.parametrize("selection", ["inherited", "explicit"])
async def test_agent_runner_does_not_grant_workspace_resource_read(skills: SkillJourney, skill_native, selection):
    native, user = skill_native
    binding = await member(native, user, "viewer")
    skill = await skills.skill()
    agent = await skills.agent()
    await grant(skills, binding, agent, "runner")
    case = await skills.case()
    body = {**skills.live.start_body(case), "agent_id": agent["agent"]["id"]}
    baseline = await user.post(skills.base + "/runs", json=body, headers={"Idempotency-Key": uuid4().hex})
    assert baseline.status_code == 202, baseline.json()
    skills.live.track(baseline.json())
    await skills.live.finish(baseline.json()["run_id"])
    await skills.revision(agent["agent"], skills=[{"skill_key": skill["skill"]["key"]}])
    await revoke_workspace(native, binding)
    assert (await user.get(skills.base + "/agents/" + agent["agent"]["id"])).status_code == 200
    assert (await user.get("/api/v1/skill-revisions/" + skill["revision"]["id"] + "/content")).status_code in {403, 404}
    # Current Model read authorization also disappears, so Run denial alone is not Skill-specific evidence.
    later = await skills.case()
    body = {**skills.live.start_body(later), "agent_id": agent["agent"]["id"]}
    if selection == "explicit":
        body["config_override"] = {"skills": [{"skill_key": skill["skill"]["key"]}]}
    before = await skills.runs()
    denied = await user.post(skills.base + "/runs", json=body, headers={"Idempotency-Key": uuid4().hex})
    if denied.status_code == 202:
        skills.live.track(denied.json())
    assert denied.status_code in {403, 404}, denied.json()
    assert await skills.runs() == before
    assert skills.observations(later) == []


@pytest.mark.parametrize("operation", ["retry", "queue"])
async def test_direct_runner_cannot_reuse_skill_after_workspace_revocation(
    skills: SkillJourney, skill_native, operation
):
    native, user = skill_native
    binding = await member(native, user, "runner")
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}])
    await grant(skills, binding, agent, "runner")
    environment, _ = await skills.environment()
    case = await skills.case(gate_at=0, steps=[proof(key)])
    body = {
        **skills.live.start_body(case),
        "agent_id": agent["agent"]["id"],
        "environment": {"environment_id": environment["id"]},
    }
    response = await user.post(skills.base + "/runs", json=body, headers={"Idempotency-Key": uuid4().hex})
    assert response.status_code == 202, response.json()
    receipt = response.json()
    skills.live.track(receipt)
    await skills.ready(case, receipt["run_id"])
    thread = await skills.live.thread(receipt["thread_id"])
    later = await skills.case(steps=[proof(key)])
    if operation == "queue":
        queued = await user.post(
            "/api/v1/threads/" + thread["id"] + "/runs",
            json={"expected_thread_version": thread["version"], "input": skills.live.start_body(later)["input"]},
            headers={"Idempotency-Key": uuid4().hex},
        )
        assert queued.status_code == 202 and queued.json()["outcome"] == "queued", queued.json()
    else:
        await skills.live.interrupt(receipt["run_id"])
        await skills.live.finish(receipt["run_id"], "cancelled")
    await revoke_workspace(native, binding)
    assert (await user.get(skills.base + "/agents/" + agent["agent"]["id"])).status_code == 200
    before = await skills.live.collection("/api/v1/threads/" + thread["id"] + "/runs")
    observed = len(skills.observations(case))
    await skills.live.release(case)
    if operation == "queue":
        queued_id = queued.json()["queued_submission"]["queued_submission_id"]
        await skills.live.finish(receipt["run_id"])
        path = "/api/v1/queued-submissions/" + queued_id
        pending = await skills.live.request("GET", path)
        assert pending["state"] == "queued" and pending.get("consumed_run_id") is None
        # Revocation is recoverable: retain editable intent and reauthorize on each drain.
        await skills.live.assert_stable(lambda: skills.live.request("GET", path), pending, seconds=3)
        assert skills.observations(later) == []
        after = await skills.live.collection("/api/v1/threads/" + thread["id"] + "/runs")
        assert [run["id"] for run in after] == [run["id"] for run in before]
        await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
        await native.post(native.base + "/role-bindings", {"principal_id": binding["principal_id"], "role": "runner"})
        settled = await skills.live.wait(
            lambda: skills.live.request("GET", path),
            lambda item: item["state"] != "queued",
            "Queued Skill invocation consumed after Workspace authority restoration",
        )
        assert settled["state"] == "consumed", settled
        successor = {"run_id": settled["consumed_run_id"], "thread_id": receipt["thread_id"]}
        skills.live.track(successor)
        completed = await skills.live.finish(successor["run_id"])
        assert "ATTACHMENT_TWO" in completed["output_text"] and "ATTACHMENT_ONE" not in completed["output_text"]
        after = await skills.live.collection("/api/v1/threads/" + thread["id"] + "/runs")
        assert {run["id"] for run in after} == {run["id"] for run in before} | {successor["run_id"]}
    else:
        thread = await skills.live.thread(receipt["thread_id"])
        denied = await user.post(
            "/api/v1/runs/" + receipt["run_id"] + "/retry",
            json={"expected_thread_version": thread["version"]},
            headers={"Idempotency-Key": uuid4().hex},
        )
        if denied.status_code == 202:
            skills.live.track(denied.json())
        assert denied.status_code in {403, 404}, denied.json()
        assert len(skills.observations(case)) == observed
        after = await skills.live.collection("/api/v1/threads/" + thread["id"] + "/runs")
        assert [run["id"] for run in after] == [run["id"] for run in before]
