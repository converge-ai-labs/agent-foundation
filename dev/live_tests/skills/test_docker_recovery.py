"""Real Docker loss during partial Skill preparation and obsolete Worker recovery."""

import signal

import anyio
import pytest
from a13n_environment import DockerSDKEngine

from ..environment.environment_workers import add_second_worker, reset_workers
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
    environment, root = await skills.environment(preparation=preparation, provider_type="a13n.docker")
    case = await skills.case(steps=[proof(key)])
    engine = DockerSDKEngine.from_env(timeout_seconds=30)
    labels = {"io.a13n.environment-provider": "a13n.docker", "io.a13n.environment-id": environment["id"]}
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
        assert not list(root.rglob(".a13n-service-complete.json"))
        targets = await engine.find_containers(labels)
        assert len(targets) == 1
        original = targets[0].container_id
        assert (await engine.inspect_container(original)).status == "running"
        await publish_skill(skills, key, "DOCUMENT_TWO", "ATTACHMENT_TWO", previous=first["skill"])
        if pair:
            stopped = pair.workers[0]
            skills.lab.send(stopped, signal.SIGSTOP)
        await engine.stop_container(original, timeout_seconds=1)
        if fault != "stop":
            await engine.remove_container(original)
            assert await engine.inspect_container(original) is None
        else:
            assert (await engine.inspect_container(original)).status == "exited"
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
            assert [p.read_text() for p in root.rglob("proof.txt")] == ["ATTACHMENT_ONE"]
            assert len(list(root.rglob(".a13n-service-complete.json"))) == 1
            evidence["manual_retry"] = {"run_id": retry["run_id"], "status": "completed", "skill_version": 1}
            private_json(evidence_path, evidence)
            pytest.fail(
                f"Automatic Docker {fault} recovery failed: {evidence['failure_code']}; "
                f"{len(attempts)} Attempts, {evidence['model_requests']} model requests. "
                "Manual Retry completed with frozen Skill version 1."
            )
        assert "ATTACHMENT_ONE" in result["output_text"] and "ATTACHMENT_TWO" not in result["output_text"]
        assert len(attempts) == 2 and attempts[-1]["status"] == "succeeded", attempts
        recovered = await engine.find_containers(labels)
        assert len(recovered) == 1
        assert (await engine.inspect_container(recovered[0].container_id)).status == "running"
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
        assert [p.read_text() for p in root.rglob("proof.txt")] == ["ATTACHMENT_ONE"]
        assert len(list(root.rglob(".a13n-service-complete.json"))) == 1
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
                for target in await engine.find_containers(labels):
                    await engine.stop_container(target.container_id, timeout_seconds=1)
                    await engine.remove_container(target.container_id)
                assert not await engine.find_containers(labels)
            finally:
                await engine.close()
