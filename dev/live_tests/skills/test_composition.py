"""Mixed catalogs and nested Agent execution preserve independent exact locks."""

import asyncio
import json
from uuid import uuid4

import pytest

from ..infrastructure.management_packages import publish_skill
from .support import SkillJourney, proof

pytestmark = pytest.mark.anyio


async def locks(skills, run_id):
    await skills.lab.command("dev.live_tests.skills.lock_probe", run_id)
    return json.loads((skills.lab.root / (run_id + "-skills.json")).read_text())


def observed_results(skills, case):
    return json.dumps(
        [
            message["content"]
            for observation in skills.observations(case)
            for message in observation["body"]["messages"]
            if message.get("role") == "tool"
        ]
    )


@pytest.mark.parametrize("change", ["one-head", "both-heads", "deleted", "recreated"])
async def test_skill_mixed_catalog_accepts_atomically(skills: SkillJourney, change):
    first, second = await skills.skill(), await skills.skill()
    a, b = first["skill"]["key"], second["skill"]["key"]
    agent = await skills.agent(skills=[])
    environment, _ = await skills.environment()
    case = await skills.case(steps=[proof(a), proof(b)])
    selection = [{"skill_key": a, "version": 1}, {"skill_key": b}]
    body = {
        **skills.live.start_body(case),
        "agent_id": agent["agent"]["id"],
        "config_override": {"skills": selection},
        "environment": {"environment_id": environment["id"]},
    }
    before = await skills.runs()
    barrier = skills.arm("skill.acceptance_prepared", agent_id=agent["agent"]["id"])
    task = asyncio.create_task(
        skills.live.http.post(skills.base + "/runs", json=body, headers={"Idempotency-Key": uuid4().hex})
    )
    try:
        await skills.reached(barrier)
        await publish_skill(skills, b, "B_DOCUMENT_TWO", "B_ATTACHMENT_TWO", previous=second["skill"])
        if change == "both-heads":
            await publish_skill(skills, a, "A_DOCUMENT_TWO", "A_ATTACHMENT_TWO", previous=first["skill"])
        if change in {"deleted", "recreated"}:
            await skills.delete_skill(first["skill"])
            if change == "recreated":
                await publish_skill(skills, a, "REPLACEMENT_DOCUMENT", "REPLACEMENT_ATTACHMENT")
        skills.release(barrier)
        response = await task
        if change in {"deleted", "recreated"}:
            assert response.status_code == 409, response.json()
            assert response.json()["error"]["details"]["reason"] == "skill_selection_invalid"
            assert await skills.runs() == before
            assert skills.observations(case) == []
        else:
            assert response.status_code == 202, response.json()
            receipt = response.json()
            skills.live.track(receipt)
            await skills.live.finish(receipt["run_id"])
            persisted = (await locks(skills, receipt["run_id"]))["skills"]
            assert [(x["skill_key"], x["version"]) for x in persisted] == [(a, 1), (b, 2)]
            results = observed_results(skills, case)
            assert "ATTACHMENT_ONE" in results and "B_ATTACHMENT_TWO" in results
            assert "A_ATTACHMENT_TWO" not in results
    finally:
        skills.release(barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mode", ["inline", "async"])
@pytest.mark.parametrize("publication", ["before-acceptance", "after-acceptance"])
async def test_skill_child_catalog_keeps_its_own_version(skills: SkillJourney, mode, publication):
    first = await skills.skill()
    key = first["skill"]["key"]
    second = await publish_skill(skills, key, "CHILD_DOCUMENT_TWO", "CHILD_ATTACHMENT_TWO", previous=first["skill"])
    child = await skills.agent(skills=[{"skill_key": key}])
    parent = await skills.agent(
        skills=[{"skill_key": key, "version": 1}],
        subagent_mode=mode,
        subagents={"child": {"agent_id": child["agent"]["id"], "environment": {"mode": "shared"}}},
    )
    environment, _ = await skills.environment()
    child_case = await skills.case(steps=[proof(key)])
    # The child gets a separate case, so its response is derived from its own file read.
    parent_case = await skills.case(
        steps=[
            proof(key),
            {
                "tool": "delegate",
                "arguments": {
                    ("subagent" if mode == "inline" else "subagent_name"): "child",
                    "prompt": "LIVE_TEST " + json.dumps(child_case),
                },
            },
        ]
    )
    barrier = skills.arm("skill.acceptance_prepared", agent_id=parent["agent"]["id"])
    body = {
        **skills.live.start_body(parent_case),
        "agent_id": parent["agent"]["id"],
        "environment": {"environment_id": environment["id"]},
    }
    # Keep execution behind a Worker barrier for the after-acceptance publication.
    worker_barrier = skills.arm("skill.file_published", role="worker", filename="SKILL.md")
    task = asyncio.create_task(
        skills.live.http.post(skills.base + "/runs", json=body, headers={"Idempotency-Key": uuid4().hex})
    )
    try:
        await skills.reached(barrier)
        if publication == "before-acceptance":
            await publish_skill(skills, key, "CHILD_DOCUMENT_THREE", "CHILD_ATTACHMENT_THREE", previous=second["skill"])
        skills.release(barrier)
        response = await task
        assert response.status_code == 202, response.json()
        receipt = response.json()
        skills.live.track(receipt)
        await skills.reached(worker_barrier)
        if publication == "after-acceptance":
            await publish_skill(skills, key, "CHILD_DOCUMENT_THREE", "CHILD_ATTACHMENT_THREE", previous=second["skill"])
        skills.release(worker_barrier)
        await skills.live.finish(receipt["run_id"])

        async def child_finished():
            return "CHILD_ATTACHMENT_" in observed_results(skills, child_case)

        await skills.live.wait(child_finished, bool, "Child read its managed Skill")
        frozen = await locks(skills, receipt["run_id"])
        expected = 3 if publication == "before-acceptance" else 2
        assert frozen["skills"][0]["version"] == 1
        assert frozen["children"][child["revision"]["id"]]["skills"][0]["version"] == expected
        parent_results = observed_results(skills, parent_case)
        assert "ATTACHMENT_ONE" in parent_results
        child_results = observed_results(skills, child_case)
        assert "CHILD_ATTACHMENT_" + ("THREE" if expected == 3 else "TWO") in child_results
        assert "ATTACHMENT_ONE" not in child_results
        # Both catalogs share an Environment but advertise distinct roots.
        if mode == "async":
            threads = await skills.live.collection(f"/api/v1/sessions/{receipt['session_id']}/threads")
            children = [t for t in threads if t["origin_run_id"] == receipt["run_id"]]
            assert len(children) == 1
            run_id = children[0]["current_run_id"]
            skills.live.runs.append(run_id)
            await skills.live.finish(run_id)
            assert (await locks(skills, run_id))["skills"][0]["version"] == expected
    finally:
        skills.release(barrier)
        skills.release(worker_barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
