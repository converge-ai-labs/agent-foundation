"""Real Workers converge on partial Skill roots and preserve exact locks after crashes."""

import signal

import pytest

from ..environment.environment_workers import add_second_worker, reset_workers
from ..infrastructure.management_packages import publish_skill
from .support import SkillJourney, proof

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
@pytest.mark.parametrize("ending", ["resume", "crash"])
async def test_shared_skill_preparation_survives_an_interrupted_writer(skills: SkillJourney, preparation, ending):
    await reset_workers(skills.lab)
    pair = await add_second_worker(skills.lab)
    first_worker, second_worker = pair.workers
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key, "version": 1}])
    environment, root = await skills.environment(preparation=preparation)
    cases = [await skills.case(steps=[proof(key)]) for _ in range(2)]
    barrier = skills.arm("skill.file_published", role="worker", pid=first_worker.pid, filename="SKILL.md")
    try:
        owner = await pair.start(first_worker, cases[0], environment, agent_id=agent["agent"]["id"])
        await skills.reached(barrier)
        assert len(list(root.rglob("SKILL.md"))) == 1
        assert not list(root.rglob("proof.txt"))
        assert not list(root.rglob(".a13n-service-complete.json"))
        assert skills.observations(cases[0]) == []
        contender = await pair.start(second_worker, cases[1], environment, agent_id=agent["agent"]["id"])
        result = await skills.live.finish(contender["run_id"])
        assert "ATTACHMENT_ONE" in result["output_text"]
        assert skills.observations(cases[0]) == []
        assert len(list(root.rglob(".a13n-service-complete.json"))) == 1
        assert [path.read_text() for path in root.rglob("proof.txt")] == ["ATTACHMENT_ONE"]
        assert len(await skills.lab.attempts(contender["run_id"])) == 1
        if ending == "crash":
            # The replacement Attempt must retain authority over a deleted Skill.
            await skills.revision(agent["agent"], skills=[])
            await skills.delete_skill(first["skill"])
            await publish_skill(skills, key, "REPLACEMENT_DOCUMENT", "REPLACEMENT_ATTACHMENT")
            await skills.lab.stop(first_worker, signal.SIGKILL)
        skills.release(barrier)
        recovered = await skills.live.finish(owner["run_id"])
        assert "ATTACHMENT_ONE" in recovered["output_text"] and "REPLACEMENT_ATTACHMENT" not in recovered["output_text"]
        attempts = await skills.lab.attempts(owner["run_id"])
        assert len(attempts) == (2 if ending == "crash" else 1)
        assert attempts[-1]["status"] == "succeeded"
        assert len(list(root.rglob(".a13n-service-complete.json"))) == 1
    finally:
        skills.release(barrier)
        pair.release_all()


@pytest.mark.parametrize("corruption", ["file", "completion", "unexpected-entry"])
async def test_skill_corruption_fails_before_any_model_request(skills: SkillJourney, corruption):
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}])
    environment, root = await skills.environment()
    case = await skills.case(steps=[proof(key)])
    receipt = await skills.start(case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]})
    assert "ATTACHMENT_ONE" in (await skills.live.finish(receipt["run_id"]))["output_text"]
    if corruption == "file":
        target = next(root.rglob("proof.txt"))
    elif corruption == "completion":
        target = next(root.rglob(".a13n-service-complete.json"))
    else:
        target = next(root.rglob("SKILL.md")).parent / "unexpected.txt"
    target.write_text("CORRUPTED")
    later = await skills.case(steps=[proof(key)])
    invalid = await skills.start(
        later, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]}
    )
    failed = await skills.live.finish(invalid["run_id"], "failed")
    assert skills.observations(later) == []
    assert target.read_text() == "CORRUPTED"
    assert failed["failure"]["code"] == "skill_materialization_invalid", (
        "Corruption was rejected before model work and left untouched, but its stable failure code was lost: "
        + failed["failure"]["code"]
    )
