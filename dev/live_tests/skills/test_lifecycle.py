"""Skill locks across ordinary turns, Retry, client feedback and waiting Continue."""

import asyncio
from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input
from ..infrastructure.management_packages import publish_skill
from ..infrastructure.management_support import client_tool, feedback_body
from .support import SkillJourney, proof

pytestmark = pytest.mark.anyio


async def test_ordinary_turn_resolves_new_head_in_the_same_thread(skills: SkillJourney):
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}])
    environment, _ = await skills.environment()
    case = await skills.case(steps=[proof(key)])
    receipt = await skills.start(case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]})
    original = await skills.live.finish(receipt["run_id"])
    assert "ATTACHMENT_ONE" in original["output_text"]
    await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
    later = await skills.case(steps=[proof(key)])
    thread = await skills.live.thread(receipt["thread_id"])
    submitted = await skills.post(
        f"/api/v1/threads/{thread['id']}/runs",
        {"expected_thread_version": thread["version"], "input": skills.live.start_body(later)["input"]},
        expected=202,
    )
    successor = submitted["run"]
    skills.live.track(successor)
    result = await skills.live.finish(successor["run_id"])
    assert result["thread_id"] == original["thread_id"]
    assert result["agent_revision_id"] == original["agent_revision_id"]
    assert "ATTACHMENT_TWO" in result["output_text"]
    assert await skills.live.run(original["id"]) == original


@pytest.mark.parametrize("operation", ["retry", "feedback", "waiting_continue"])
@pytest.mark.parametrize("scenario", ["new-version", "deleted", "recreated", "delete-wins", "successor-wins"])
async def test_successor_preserves_skill_lock_and_rechecks_deletion(skills: SkillJourney, operation, scenario):
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}], client_tools=[client_tool()])
    environment, _ = await skills.environment()
    if operation == "retry":
        case = await skills.case(gate_at=0, steps=[proof(key)])
    else:
        case = await skills.case(
            steps=[{"tool": "live_client", "arguments": {"prompt": "Continue reading the Skill"}}, proof(key)]
        )
    receipt = await skills.start(case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]})
    if operation == "retry":
        await skills.ready(case, receipt["run_id"])
        await skills.live.interrupt(receipt["run_id"])
        original = await skills.live.finish(receipt["run_id"], "cancelled")
        await skills.live.release(case)
    else:
        original = await skills.live.finish(receipt["run_id"], "waiting")
    thread = await skills.live.thread(receipt["thread_id"])
    path = f"/api/v1/runs/{original['id']}/{operation}"
    if operation == "feedback":
        body = await feedback_body(skills.live, original, {"ready": True})
    elif operation == "waiting_continue":
        path = f"/api/v1/threads/{thread['id']}/runs"
        body = {
            "expected_thread_version": thread["version"],
            "input": agent_input("Continue after resolving the pending client action."),
            "waiting_resolution": {
                "mode": "defaults",
                "sealed_state_digest_sha256": original["sealed_state_digest_sha256"],
            },
        }
    else:
        body = {"expected_thread_version": thread["version"]}
    if scenario != "new-version":
        await skills.revision(agent["agent"], skills=[], client_tools=[client_tool()])
    if scenario in {"deleted", "recreated"}:
        await skills.delete_skill(first["skill"])
        if scenario == "recreated":
            replacement = await publish_skill(skills, key, "REPLACEMENT_DOCUMENT", "REPLACEMENT_ATTACHMENT")
            assert replacement["skill"]["id"] != first["skill"]["id"]
    elif scenario == "new-version":
        await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
    observations = len(skills.observations(case))
    runs = await skills.live.collection(f"/api/v1/threads/{thread['id']}/runs")
    request_key = uuid4().hex
    if scenario in {"delete-wins", "successor-wins"}:
        http_response = await _race_deletion(skills, first["skill"], agent["agent"], path, body, request_key, scenario)
    else:
        http_response = await skills.live.http.post(path, json=body, headers={"Idempotency-Key": request_key})
    response = http_response.json()
    deleted = scenario in {"deleted", "recreated", "delete-wins"}
    if deleted and http_response.status_code == 202:
        unexpected = response["run"] if operation == "waiting_continue" else response
        skills.live.track(unexpected)
        settled = await skills.live.wait(
            lambda: skills.live.run(unexpected["run_id"]),
            lambda run: run["status"] not in {"accepted", "running"},
            "Unexpected successor after Skill deletion",
        )
        pytest.fail(
            f"{operation} accepted a new Run after Skill deletion: HTTP 202, status={settled['status']}, "
            f"read_deleted_attachment={'ATTACHMENT_ONE' in (settled.get('output_text') or '')}"
        )
    assert http_response.status_code == (409 if deleted else 202)
    if deleted:
        assert response["error"]["code"] == "agent_revision_not_executable"
        assert response["error"]["details"]["reason"] == "skill_selection_invalid"
        assert len(skills.observations(case)) == observations
        assert await skills.live.collection(f"/api/v1/threads/{thread['id']}/runs") == runs
    else:
        successor = response["run"] if operation == "waiting_continue" else response
        skills.live.track(successor)
        result = await skills.live.finish(successor["run_id"])
        assert "ATTACHMENT_ONE" in result["output_text"] and "ATTACHMENT_TWO" not in result["output_text"]
        assert result["agent_revision_id"] == original["agent_revision_id"]
        if scenario == "new-version":
            await skills.revision(agent["agent"], skills=[], client_tools=[client_tool()])
            await skills.delete_skill(first["skill"])
        settled_runs = await skills.live.collection(f"/api/v1/threads/{thread['id']}/runs")
        settled_observations = len(skills.observations(case))
        replay = await skills.post(path, body, key=request_key, expected=202)
        assert replay == response
        assert await skills.live.collection(f"/api/v1/threads/{thread['id']}/runs") == settled_runs
        assert len(skills.observations(case)) == settled_observations
    assert await skills.live.run(original["id"]) == original


async def _race_deletion(skills, skill, agent, path, body, request_key, scenario):
    async def submit():
        return await skills.live.http.post(path, json=body, headers={"Idempotency-Key": request_key})

    if scenario == "delete-wins":
        barrier = skills.arm("skill.acceptance_prepared", agent_id=agent["id"])
        task = asyncio.create_task(submit())
    else:
        barrier = skills.arm("skill.before_delete", skill_id=skill["id"])
        task = asyncio.create_task(skills.delete_skill(skill))
    try:
        await skills.reached(barrier)
        assert not task.done()
        if scenario == "delete-wins":
            await skills.delete_skill(skill)
            skills.release(barrier)
            return await task
        response = await submit()
        skills.release(barrier)
        await task
        return response
    finally:
        skills.release(barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
