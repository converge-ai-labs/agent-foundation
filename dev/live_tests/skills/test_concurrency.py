"""Both commit orders for Skill deletion racing accepted Runs and current Agent bindings."""

import asyncio
from uuid import uuid4

import pytest

from ..infrastructure.round_two_resources import agent_config
from .support import SkillJourney, proof

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("operation", ["run", "binding", "unarchive"])
@pytest.mark.parametrize("winner", ["delete", "operation"])
async def test_skill_delete_linearizes_with_new_authority(skills: SkillJourney, operation, winner):
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}] if operation == "unarchive" else [])
    agent_path = skills.base + "/agents/" + agent["agent"]["id"]
    case = await skills.case(steps=[proof(key)])
    headers = {"Idempotency-Key": uuid4().hex}
    if operation == "run":
        environment, _ = await skills.environment()
        path = skills.base + "/runs"
        body = {
            **skills.live.start_body(case),
            "agent_id": agent["agent"]["id"],
            "config_override": {"skills": [{"skill_key": key}]},
            "environment": {"environment_id": environment["id"]},
        }
        point, success = "skill.invocation_prepared", 202
    elif operation == "binding":
        path = agent_path + "/revisions"
        headers["If-Match"] = (await skills.live.http.get(agent_path)).headers["etag"]
        body = {"config": agent_config(skills=[{"skill_key": key}])}
        point, success = "skill.binding_prepared", 201
    else:
        await skills.lifecycle(agent["agent"], "disable")
        await skills.lifecycle(agent["agent"], "archive")
        head = await skills.live.http.get(agent_path)
        headers["If-Match"] = head.headers["etag"]
        path, body = agent_path + "/unarchive", {}
        point, success = "skill.invocation_prepared", 200

    skill_path = "/api/v1/skills/" + first["skill"]["id"]
    head = await skills.live.http.get(skill_path)
    delete_headers = {"If-Match": head.headers["etag"]}
    if winner == "delete":
        barrier = skills.arm(point, agent_id=agent["agent"]["id"])
        task = asyncio.create_task(skills.live.http.post(path, headers=headers, json=body))
    else:
        barrier = skills.arm("skill.before_delete", skill_id=first["skill"]["id"])
        task = asyncio.create_task(skills.live.http.delete(skill_path, headers=delete_headers))
    try:
        await skills.reached(barrier)
        assert not task.done()
        if winner == "delete":
            deleted = await skills.live.http.delete(skill_path, headers=delete_headers)
            assert deleted.status_code == 204
            skills.release(barrier)
            rejected = await task
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == (
                "agent_revision_create_failed" if operation == "binding" else "agent_revision_not_executable"
            )
            assert skills.observations(case) == []
            current = (await skills.live.http.get(agent_path)).json()
            assert current["default_revision_id"] == agent["revision"]["id"]
            if operation == "unarchive":
                assert current["archived_at"] is not None
        else:
            accepted = await skills.live.http.post(path, headers=headers, json=body)
            assert accepted.status_code == success, accepted.status_code
            if operation == "run":
                skills.live.track(accepted.json())
            skills.release(barrier)
            deleted = await task
            if operation == "run":
                assert deleted.status_code == 204
                result = await skills.live.finish(accepted.json()["run_id"])
                assert "ATTACHMENT_ONE" in result["output_text"]
            else:
                assert deleted.status_code == 409 and deleted.json()["error"]["code"] == "skill_in_use"
                references = await skills.live.collection(skill_path + "/references")
                assert [reference["agent_id"] for reference in references] == [agent["agent"]["id"]]
    finally:
        skills.release(barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_disabled_agent_still_blocks_skill_deletion(skills: SkillJourney):
    first = await skills.skill()
    agent = await skills.agent(skills=[{"skill_key": first["skill"]["key"], "version": 1}])
    disabled = await skills.lifecycle(agent["agent"], "disable")
    assert disabled["enabled"] is False
    path = "/api/v1/skills/" + first["skill"]["id"]
    head = await skills.live.http.get(path)
    deleted = await skills.live.http.delete(path, headers={"If-Match": head.headers["etag"]})
    assert deleted.status_code == 409 and deleted.json()["error"]["code"] == "skill_in_use"
    await skills.lifecycle(disabled, "archive")
    assert await skills.live.collection(path + "/references") == []
    await skills.delete_skill(first["skill"])
