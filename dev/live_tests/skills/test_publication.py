"""Publication ordering across both transaction-free Run preparation boundaries."""

import asyncio
from uuid import uuid4

import pytest

from ..infrastructure.management_packages import publish_skill
from .support import SkillJourney, proof

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("pinned", [False, True], ids=["current", "pinned"])
@pytest.mark.parametrize(
    "boundary", ["before-request", "invocation_prepared", "acceptance_prepared", "after-acceptance"]
)
async def test_skill_publication_orders_with_run_acceptance(skills: SkillJourney, pinned, boundary):
    first = await skills.skill()
    key = first["skill"]["key"]
    selection = {"skill_key": key, **({"version": 1} if pinned else {})}
    agent = await skills.agent(skills=[selection])
    environment, _ = await skills.environment()
    case = await skills.case(steps=[proof(key)])
    body = {
        **skills.live.start_body(case),
        "agent_id": agent["agent"]["id"],
        "environment": {"environment_id": environment["id"]},
    }
    headers = {"Idempotency-Key": uuid4().hex}

    async def publish():
        return await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])

    barrier = None
    task = None
    try:
        if boundary == "before-request":
            await publish()
        elif boundary != "after-acceptance":
            barrier = skills.arm("skill." + boundary, agent_id=agent["agent"]["id"])
        task = asyncio.create_task(skills.live.http.post(skills.base + "/runs", headers=headers, json=body))
        if barrier:
            await skills.reached(barrier)
            assert not task.done()
            await publish()
            skills.release(barrier)
        response = await task
        assert response.status_code == 202, (boundary, pinned, response.status_code, response.json())
        receipt = response.json()
        skills.live.track(receipt)
        if boundary == "after-acceptance":
            await publish()
        result = await skills.live.finish(receipt["run_id"])
        # Selection observes the default once; later publication cannot replace
        # the prepared snapshot, even before the acceptance transaction commits.
        expected = "TWO" if not pinned and boundary == "before-request" else "ONE"
        assert "ATTACHMENT_" + expected in result["output_text"], result["output_text"]
        other = "TWO" if expected == "ONE" else "ONE"
        assert "ATTACHMENT_" + other not in result["output_text"]
        replay = await skills.live.http.post(skills.base + "/runs", headers=headers, json=body)
        assert replay.status_code == 202, replay.text
        await skills.live.assert_current_acceptance(replay.json(), receipt)
        if not pinned:
            fresh = await skills.post(skills.base + "/runs", body, expected=202)
            skills.live.track(fresh)
            assert fresh["run_id"] != receipt["run_id"]
            assert "ATTACHMENT_TWO" in (await skills.live.finish(fresh["run_id"]))["output_text"]
    finally:
        if barrier:
            skills.release(barrier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("operation", ["continue", "fork", "queue"])
async def test_skill_publication_refreshes_successor_state(skills: SkillJourney, operation):
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}])
    environment, _ = await skills.environment()
    source_case = await skills.case(steps=[proof(key)], **({"gate_at": 0} if operation == "queue" else {}))
    source = await skills.start(
        source_case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]}
    )
    if operation == "queue":
        await skills.ready(source_case, source["run_id"])
    else:
        await skills.live.finish(source["run_id"])
    later = await skills.case(steps=[proof(key)])
    thread = await skills.live.thread(source["thread_id"])
    if operation == "fork":
        path = f"/api/v1/runs/{source['run_id']}/fork"
        body = {"input": skills.live.start_body(later)["input"], "config_override": {}}
    else:
        path = f"/api/v1/threads/{thread['id']}/runs"
        body = {"expected_thread_version": thread["version"], "input": skills.live.start_body(later)["input"]}
    barriers = [
        skills.arm("skill.acceptance_prepared", role=role, agent_id=agent["agent"]["id"])
        for role in (("control", "worker") if operation == "queue" else ("control",))
    ]

    async def prepared():
        return any((barrier / "hit-1.json").exists() for barrier in barriers)

    task = None
    try:
        if operation == "queue":
            response = await skills.post(path, body, expected=202)
            assert response["outcome"] == "queued"
            queued = response["queued_submission"]
            await skills.live.release(source_case)
        else:
            task = asyncio.create_task(skills.live.http.post(path, json=body, headers={"Idempotency-Key": uuid4().hex}))
        await skills.live.wait(prepared, bool, "Skill successor acceptance barrier")
        await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
        for barrier in barriers:
            skills.release(barrier)
        if operation == "queue":
            settled = await skills.live.wait(
                lambda: skills.live.request("GET", "/api/v1/queued-submissions/" + queued["queued_submission_id"]),
                lambda value: value["state"] != "queued",
                "Skill queue consumption",
            )
            assert settled["state"] == "consumed", settled
            run_id = settled["consumed_run_id"]
            skills.live.track({"run_id": run_id, "thread_id": source["thread_id"]})
        else:
            response = await task
            assert response.status_code == 202, response.json()
            receipt = response.json() if operation == "fork" else response.json()["run"]
            skills.live.track(receipt)
            run_id = receipt["run_id"]
        assert "ATTACHMENT_TWO" in (await skills.live.finish(run_id))["output_text"]
    finally:
        for barrier in barriers:
            skills.release(barrier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("change", ["deleted", "recreated", "agent", "continuous"])
async def test_skill_publication_retry_keeps_identity_and_is_bounded(skills: SkillJourney, change):
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[])
    environment, _ = await skills.environment()
    case = await skills.case(steps=[proof(key)])
    body = {
        **skills.live.start_body(case),
        "agent_id": agent["agent"]["id"],
        "config_override": {"skills": [{"skill_key": key}]},
        "environment": {"environment_id": environment["id"]},
    }
    barrier = skills.arm("skill.acceptance_prepared", agent_id=agent["agent"]["id"])
    task = asyncio.create_task(
        skills.live.http.post(skills.base + "/runs", json=body, headers={"Idempotency-Key": uuid4().hex})
    )
    before = await skills.runs()
    try:
        await skills.reached(barrier)
        current = await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
        next_barrier = skills.arm("skill.acceptance_prepared", agent_id=agent["agent"]["id"])
        skills.release(barrier)
        barrier = next_barrier
        await skills.reached(barrier)
        if change in {"deleted", "recreated"}:
            await skills.delete_skill(current["skill"])
            if change == "recreated":
                replacement = await publish_skill(skills, key, "REPLACEMENT_DOCUMENT", "REPLACEMENT_ATTACHMENT")
                assert replacement["skill"]["id"] != first["skill"]["id"]
        elif change == "agent":
            await skills.revision(agent["agent"], skills=[], instructions="A new Agent revision")
        else:
            current = await publish_skill(skills, key, "DOCUMENT_THREE", "ATTACHMENT_THREE", previous=current["skill"])
            next_barrier = skills.arm("skill.acceptance_prepared", agent_id=agent["agent"]["id"])
            skills.release(barrier)
            barrier = next_barrier
            await skills.reached(barrier)
            await publish_skill(skills, key, "DOCUMENT_FOUR", "ATTACHMENT_FOUR", previous=current["skill"])
        skills.release(barrier)
        response = await task
        assert response.status_code == 409, response.json()
        if change == "continuous":
            assert response.json()["error"]["code"] == "run_invocation_changed"
        elif change in {"deleted", "recreated"}:
            assert response.json()["error"]["details"]["reason"] == "skill_selection_invalid"
        assert skills.observations(case) == []
        assert await skills.runs() == before
    finally:
        skills.release(barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
