"""Partial preparation, real storage cuts, and resumed obsolete Worker ownership."""

import signal
from uuid import uuid4

import pytest

from ..environment.environment_workers import add_second_worker, reset_workers
from ..infrastructure.management_packages import publish_skill
from ..infrastructure.run_faults import arm
from .support import SkillJourney, proof
from .test_composition import locks

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
@pytest.mark.parametrize("fault", ["storage", "environment-stale", "cancel", "old-worker"])
async def test_skill_preparation_failure_preserves_frozen_content(skills: SkillJourney, preparation, fault):
    await reset_workers(skills.lab)
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}])
    environment, root = await skills.environment(preparation=preparation)
    case = await skills.case(steps=[proof(key)])
    barriers = []
    pair = None
    stopped = None
    try:
        if fault == "storage":
            barrier = skills.arm("skill.before_package_read", role="worker", skill_key=key)
            failed_read = skills.arm("skill.package_read_failed", role="worker", skill_key=key)
            barriers += [barrier, failed_read]
        elif fault == "environment-stale":
            barrier = arm(
                skills.lab.root / "faults",
                uuid4().hex,
                point="skill.environment_stale",
                role="worker",
                action="observe",
                match={"filename": "proof.txt"},
            )
            barriers.append(barrier)
        else:
            if fault == "old-worker":
                pair = await add_second_worker(skills.lab)
            barrier = skills.arm(
                "skill.file_published",
                role="worker",
                filename="SKILL.md",
                **({"pid": pair.workers[0].pid} if pair else {}),
            )
            barriers.append(barrier)
        if pair:
            receipt = await pair.start(pair.workers[0], case, environment, agent_id=agent["agent"]["id"])
        else:
            receipt = await skills.start(
                case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]}
            )
        await skills.reached(barrier)
        assert skills.observations(case) == []
        assert not list(root.rglob(".a13n-service-complete.json"))
        await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
        if fault == "storage":
            proxy = skills.lab.proxies["objects"]
            rejected = proxy.rejected
            proxy.cut()
            skills.release(barrier)
            await skills.reached(failed_read)
            assert proxy.rejected > rejected
            assert skills.observations(case) == []
            proxy.restore()
            skills.release(failed_read)
        elif fault == "cancel":
            await skills.live.interrupt(receipt["run_id"])
            skills.release(barrier)
        elif fault == "old-worker":
            stopped = pair.workers[0]
            skills.lab.send(stopped, signal.SIGSTOP)
            # Production lease expiry, scheduler fencing and a real second Worker own recovery.
            result = await skills.live.finish(receipt["run_id"])
            assert "ATTACHMENT_ONE" in result["output_text"]
            attempts = await skills.lab.attempts(receipt["run_id"])
            assert len(attempts) == 2 and attempts[-1]["status"] == "succeeded"
            observed_count = len(skills.observations(case))
            skills.release(barrier)
            skills.lab.send(stopped, signal.SIGCONT)
            stopped = None

            async def released():
                return (barrier / "finished-1.json").exists()

            await skills.live.wait(released, bool, "Obsolete Worker left materialization barrier")
            await skills.live.assert_stable(lambda: skills.live.run(receipt["run_id"]), result, seconds=2)
            assert len(skills.observations(case)) == observed_count
            assert [p.read_text() for p in root.rglob("proof.txt")] == ["ATTACHMENT_ONE"]
            assert len(list(root.rglob(".a13n-service-complete.json"))) == 1
            assert (await locks(skills, receipt["run_id"]))["skills"][0]["version"] == 1
            return
        if fault in {"storage", "environment-stale"}:
            completed = await skills.live.finish(receipt["run_id"])
            attempts = await skills.lab.attempts(receipt["run_id"])
            assert len(attempts) == 2 and attempts[-1]["status"] == "succeeded"
            retry = receipt
        else:
            await skills.live.finish(receipt["run_id"], "cancelled")
            assert skills.observations(case) == []
            assert not list(root.rglob(".a13n-service-complete.json"))
            thread = await skills.live.thread(receipt["thread_id"])
            retry = await skills.post(
                f"/api/v1/runs/{receipt['run_id']}/retry", {"expected_thread_version": thread["version"]}, expected=202
            )
            skills.live.track(retry)
            completed = await skills.live.finish(retry["run_id"])
        assert "ATTACHMENT_ONE" in completed["output_text"] and "ATTACHMENT_TWO" not in completed["output_text"]
        assert (await locks(skills, retry["run_id"]))["skills"][0]["version"] == 1
        assert [p.read_text() for p in root.rglob("proof.txt")] == ["ATTACHMENT_ONE"]
        assert len(list(root.rglob(".a13n-service-complete.json"))) == 1
    finally:
        skills.lab.proxies["objects"].restore()
        for barrier in barriers:
            skills.release(barrier)
        if stopped:
            skills.lab.send(stopped, signal.SIGCONT)
        if pair:
            pair.release_all()
