"""Provider-independent Service races exercised against real native lifecycles."""

import asyncio
import json
import logging
import signal
from datetime import datetime, timedelta

import anyio
import pytest

from .e2b_support import eventually, past
from .lifecycle_support import lifecycle_barrier, shell

logger = logging.getLogger(__name__)


class LifecycleCases:
    async def test_service_idle_retention_stops_then_deletes_without_waking(self, service):
        journey = service.journey
        environment = await service.allocate(stop_after=5, delete_after=45)
        case = await journey.case(gate_at=0, steps=[shell("printf active")])
        receipt = await journey.start(case, environment={"environment_id": environment["id"]})
        await journey.ready(case, receipt["run_id"])
        target = await service.target(environment)
        active = await service.record(environment)
        await past(datetime.fromisoformat(active["condition_since"]) + timedelta(seconds=8))
        await service.pool.state(target, "running")
        assert (await service.record(environment))["status"] == "running"
        await journey.live.release(case)
        await journey.live.finish(receipt["run_id"])
        stopped = await service.status(environment, "stopped")
        await service.pool.state(target, service.stopped_status)
        stopped_since = stopped["condition_since"]

        async def deleted_without_waking():
            row = await service.record(environment)
            native = await service.native_status(target)
            assert native in {"absent", service.stopped_status}, "Retention woke a paused target"
            assert row["condition_since"] == stopped_since, "Stop reset the deletion clock"
            return row

        deleted = await eventually(
            deleted_without_waking, lambda row: row["status"] == "deleted", "Idle native deletion"
        )
        assert deleted["generation"] == stopped["generation"]
        await service.pool.state(target, "absent")

    async def test_service_concurrent_runs_prepare_one_native_target(self, service):
        journey = service.journey
        environment = await service.allocate(preparation="on_use")
        cases = [await journey.case(gate_at=0, steps=[shell("printf concurrent")]) for _ in range(2)]
        receipts = await asyncio.gather(
            *(journey.start(case, environment={"environment_id": environment["id"]}) for case in cases)
        )
        await asyncio.gather(
            *(journey.ready(case, receipt["run_id"]) for case, receipt in zip(cases, receipts, strict=True))
        )
        assert (await service.record(environment))["state"] is None
        assert await service.native_targets(environment) == []
        await asyncio.gather(*(journey.live.release(case) for case in cases))
        results = await asyncio.gather(*(journey.live.finish(receipt["run_id"]) for receipt in receipts))
        assert all("concurrent" in result["output_text"] for result in results)
        row = await service.record(environment)
        assert row["generation"] == 1
        targets = await service.native_targets(environment)
        assert targets == [await service.target(environment)]
        logger.info("Concurrent Runs shared native target=%s generation=1", targets[0])

    async def test_service_worker_crash_recovers_create_before_state_publication(self, service):
        journey, lab = service.journey, service.lab
        environment = await service.allocate()
        worker = lab.workers[-1]
        with lifecycle_barrier(lab, environment, "prepare", provider=service.provider) as root:
            case = await journey.case(steps=[shell("printf recovered-create")])
            crashed = await journey.start(case, environment={"environment_id": environment["id"]})
            await eventually(
                lambda: anyio.Path(root / "claimed").exists(), bool, "Worker created native before publication"
            )
            evidence = json.loads((root / "claimed").read_text())
            target = evidence["target_id"]
            service.track_target(target)
            unpublished = await service.record(environment)
            assert unpublished["state"] is None and unpublished["operation_id"]
            await lab.stop(worker, signal.SIGKILL)
            await journey.live.interrupt(crashed["run_id"])
            await lab.start_worker()
            repaired = await eventually(
                lambda: service.record(environment),
                lambda row: row["state"] is not None and row["operation_id"] is None,
                "Successor Worker reconciled the abandoned native create",
            )
            assert repaired["state"]["state"][service.native_key] == target and repaired["generation"] == 1
            assert await service.native_targets(environment) == [target]
            receipt = await journey.start(
                await journey.case(steps=[shell("printf recovered-create")]),
                environment={"environment_id": environment["id"]},
            )
            assert "recovered-create" in (await journey.live.finish(receipt["run_id"]))["output_text"]
            assert await service.target(environment) == target

    @pytest.mark.parametrize("action", ["stop", "delete"])
    async def test_service_new_use_waits_for_retention_effect_publication(self, service, action):
        journey = service.journey
        retention = {"stop_after": 5} if action == "stop" else {"delete_after": 5}
        environment = await service.allocate(**retention)
        selection = {"environment_id": environment["id"]}
        first = await journey.start(await journey.case(steps=[shell("printf initial")]), environment=selection)
        await journey.live.finish(first["run_id"])
        target = await service.target(environment)
        original = await service.record(environment)
        with lifecycle_barrier(service.lab, environment, action, provider=service.provider) as root:
            await eventually(lambda: anyio.Path(root / "claimed").exists(), bool, "Retention native effect completed")
            await service.pool.state(target, service.stopped_status if action == "stop" else "absent")
            pending = await service.record(environment)
            assert pending["operation_id"] and pending["state"] == original["state"]
            case = await journey.case(gate_at=0, steps=[shell("printf after-retention")])
            receipt = await journey.start(case, environment=selection)
            # Require a live Attempt to contend for the lease, not just an unclaimed queued Run.
            await journey.live.wait(
                lambda: journey.live.run(receipt["run_id"]),
                lambda run: run["status"] == "running",
                "New Run acquired an Attempt during retention",
            )
            for _ in range(3):
                row = await service.record(environment)
                assert row["operation_id"] == pending["operation_id"] and row["generation"] == original["generation"]
                assert journey.observations(case) == [], "Model ran before Environment preparation completed"
                await anyio.sleep(1)
            (root / "release").touch()
            await journey.ready(case, receipt["run_id"])
            renewed = await service.record(environment)
            successor = await service.target(environment)
            assert renewed["operation_id"] is None
            assert renewed["generation"] == original["generation"] + (action == "delete")
            assert (successor == target) == (action == "stop")
            await service.pool.state(successor, "running")
            await journey.live.release(case)
            assert "after-retention" in (await journey.live.finish(receipt["run_id"]))["output_text"]
            logger.info(
                "native %s/new-use race old=%s new=%s generation=%s", action, target, successor, renewed["generation"]
            )
