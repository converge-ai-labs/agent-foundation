"""Real Docker loss during partial Skill preparation and obsolete Worker recovery."""

import json
import signal

import anyio
import pytest

from ..environment.environment_workers import add_second_worker, reset_workers
from ..environment.test_51_docker_service_lifecycle import DockerTargets
from ..infrastructure.management_packages import publish_skill
from ..infrastructure.round_two_lab import private_json
from ..protocol.stream import assert_stream
from .support import SkillJourney, proof
from .test_composition import locks

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
@pytest.mark.parametrize("fault", ["stop", "remove", "obsolete-worker"])
async def test_docker_loss_preserves_frozen_skill(skills: SkillJourney, preparation, fault):
    await reset_workers(skills.lab)
    (skills.lab.root / "faults").mkdir(exist_ok=True)
    (skills.lab.root / "faults" / "observe-environment").touch()
    first = await skills.skill()
    key = first["skill"]["key"]
    agent = await skills.agent(skills=[{"skill_key": key}])
    environment, _ = await skills.environment(preparation=preparation, provider_type="a13n.docker")
    case = await skills.case(steps=[proof(key)])
    pool = DockerTargets()
    pool.identities.add(environment["id"])
    pair = None
    stopped = None
    barrier = None
    try:
        if fault == "obsolete-worker":
            pair = await add_second_worker(skills.lab)
        barrier = skills.arm(
            "skill.file_published",
            role="worker",
            filename="SKILL.md",
            **({"pid": pair.workers[0].pid} if pair else {}),
        )
        if pair:
            receipt = await pair.start(pair.workers[0], case, environment, agent_id=agent["agent"]["id"])
        else:
            receipt = await skills.start(
                case, agent_id=agent["agent"]["id"], environment={"environment_id": environment["id"]}
            )
        await skills.reached(barrier)
        assert skills.observations(case) == []
        targets = await pool.targets(environment["id"])
        assert len(targets) == 1
        original = targets[0].container_id
        assert not (await storage_evidence(pool, original))["markers"]
        pool.container_ids.add(original)
        assert (await pool.info(original)).status == "running"
        await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
        if pair:
            stopped = pair.workers[0]
            skills.lab.send(stopped, signal.SIGSTOP)
        container = await anyio.to_thread.run_sync(pool.engine.client.containers.get, original)
        await anyio.to_thread.run_sync(lambda: container.stop(timeout=1))
        if fault != "stop":
            await pool.remove(original)
            assert await pool.info(original) is None
        else:
            assert (await pool.info(original)).status == "exited"
        if not pair:
            skills.release(barrier)
        result = await skills.live.wait(
            lambda: skills.live.run(receipt["run_id"]),
            lambda run: run["status"] in {"completed", "failed", "cancelled"},
            "Skill Docker recovery settled",
        )
        attempts = await skills.lab.attempts(receipt["run_id"])
        evidence = {
            "preparation": preparation,
            "fault": fault,
            "run_id": receipt["run_id"],
            "status": result["status"],
            "failure_code": (result.get("failure") or {}).get("code"),
            "attempts": [{"id": a["id"], "number": a["attempt_number"], "status": a["status"]} for a in attempts],
            "model_requests": len(skills.observations(case)),
        }
        evidence_path = skills.lab.root / ("docker-" + fault + "-" + preparation + ".json")
        private_json(evidence_path, evidence)
        if result["status"] != "completed":
            assert not pair, evidence
            thread = await skills.live.thread(receipt["thread_id"])
            retry = await skills.post(
                f"/api/v1/runs/{receipt['run_id']}/retry",
                {"expected_thread_version": thread["version"]},
                expected=202,
            )
            skills.live.track(retry)
            retried = await skills.live.finish(retry["run_id"])
            assert "ATTACHMENT_ONE" in retried["output_text"] and "ATTACHMENT_TWO" not in retried["output_text"]
            assert (await locks(skills, retry["run_id"]))["skills"][0]["version"] == 1
            target = (await pool.targets(environment["id"]))[0]
            assert_storage(await storage_evidence(pool, target.container_id))
            evidence["manual_retry"] = {"run_id": retry["run_id"], "status": "completed", "skill_version": 1}
            private_json(evidence_path, evidence)
            pytest.fail(
                f"Automatic Docker {fault} recovery failed: {evidence['failure_code']}; "
                f"{len(attempts)} Attempts, {evidence['model_requests']} model requests. "
                "Manual Retry completed with frozen Skill version 1."
            )
        assert "ATTACHMENT_ONE" in result["output_text"] and "ATTACHMENT_TWO" not in result["output_text"]
        assert len(attempts) == 2 and attempts[-1]["status"] == "succeeded", attempts
        recovered = await pool.targets(environment["id"])
        assert len(recovered) == 1
        pool.container_ids.add(recovered[0].container_id)
        assert (await pool.info(recovered[0].container_id)).status == "running"
        if fault != "stop":
            assert recovered[0].container_id != original
        if pair:
            observed_count = len(skills.observations(case))
            skills.release(barrier)
            skills.lab.send(stopped, signal.SIGCONT)
            stopped = None

            async def released():
                return (barrier / "finished-1.json").exists()

            await skills.live.wait(released, bool, "Obsolete Docker Worker left materialization barrier")
            await skills.live.assert_stable(lambda: skills.live.run(receipt["run_id"]), result, seconds=2)
            assert len(skills.observations(case)) == observed_count
        assert (await locks(skills, receipt["run_id"]))["skills"][0]["version"] == 1
        assert_storage(await storage_evidence(pool, recovered[0].container_id))
        events = await skills.live.events(receipt["run_id"])
        assert_stream(events, receipt["run_id"])
        assert await skills.live.events(receipt["run_id"], after=events[-1].cursor) == []
    finally:
        with anyio.CancelScope(shield=True):
            if barrier:
                skills.release(barrier)
            if stopped:
                skills.lab.send(stopped, signal.SIGCONT)
            if pair:
                pair.release_all()
            await skills.live.cleanup()
            # Stop only lab-owned Workers before removing exact-label test targets.
            for worker in skills.lab.workers:
                if worker.returncode is None:
                    await skills.lab.stop(worker)
            try:
                for target in await pool.targets(environment["id"]):
                    await pool.remove(target.container_id)
                assert not await pool.targets(environment["id"])
            finally:
                await pool.engine.close()


async def storage_evidence(pool, container_id):
    def read():
        container = pool.engine.client.containers.get(container_id)
        result = container.exec_run(
            [
                "python3",
                "-c",
                "import json,pathlib; p=pathlib.Path('/workspace'); "
                "print(json.dumps({'proofs':[f.read_text() for f in p.rglob('proof.txt')],"
                "'markers':[str(f) for f in p.rglob('.a13n-service-complete.json')]}))",
            ]
        )
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    return await anyio.to_thread.run_sync(read)


def assert_storage(evidence):
    assert evidence["proofs"] == ["ATTACHMENT_ONE"]
    assert len(evidence["markers"]) == 1
