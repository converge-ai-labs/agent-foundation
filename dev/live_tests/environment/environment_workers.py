"""Two owned Worker processes, explicit claim placement, and native evidence."""

import asyncio
import json
import logging
import signal
from contextlib import asynccontextmanager
from uuid import uuid4

from ..infrastructure.management_support import ManagementJourney, last_tool_result
from ..infrastructure.run_faults import arm
from .e2b_support import eventually

logger = logging.getLogger(__name__)


class WorkerPair:
    def __init__(self, lab, workers):
        self.lab, self.workers = lab, workers
        self.journey = ManagementJourney(lab)
        self.root = lab.root / "environment-workers"
        self.identities = {
            worker: json.loads((self.root / f"worker-{worker.pid}.json").read_text())["worker_id"] for worker in workers
        }
        assert len(set(self.identities.values())) == len(workers) == 2
        assert len({worker.pid for worker in workers}) == 2

    @asynccontextmanager
    async def only(self, worker):
        others = [item for item in self.workers if item is not worker and item.returncode is None]
        for item in others:
            (self.root / f"claims-paused-{item.pid}").touch()
        try:
            yield
        finally:
            for item in others:
                (self.root / f"claims-paused-{item.pid}").unlink(missing_ok=True)

    async def start(self, worker, case, environment, **options):
        async with self.only(worker):
            receipt = await self.journey.start(case, environment={"environment_id": environment["id"]}, **options)
            await self.assert_owner(worker, receipt)
        return receipt

    async def assert_owner(self, worker, receipt):
        attempts = await self.journey.live.wait(
            lambda: self.journey.live.request("GET", f"/__live__/runs/{receipt['run_id']}/workers"),
            bool,
            "Selected Worker claimed Run",
        )
        assert attempts[0]["worker_id"] == self.identities[worker], attempts
        logger.info(
            "Environment Worker owner pid=%s worker=%s run=%s", worker.pid, attempts[0]["worker_id"], receipt["run_id"]
        )

    async def execute(self, worker, environment, steps, **options):
        case = await self.journey.case(steps=steps)
        receipt = await self.start(worker, case, environment, **options)
        run = await self.journey.live.finish(receipt["run_id"])
        result = last_tool_result(self.journey.observations(case)[-1])
        return run, result

    async def record(self, environment):
        return await self.journey.live.request("GET", f"/__live__/environments/{environment['id']}/lifecycle")

    async def command(self, environment, action):
        """A known busy admission may race another maintainer; preserve one key."""
        key, command_id = uuid4().hex, None

        async def completed():
            nonlocal command_id
            if command_id is None:
                response = await self.journey.live.http.post(
                    f"/api/v1/environments/{environment['id']}/{action}",
                    headers={"Idempotency-Key": key},
                    json={},
                )
                if response.status_code == 409 and response.json().get("error", {}).get("code") == "environment_busy":
                    return None
                assert response.status_code == 202, f"{action}: HTTP {response.status_code}"
                command_id = response.json()["id"]
            command = await self.journey.live.request("GET", f"/api/v1/environment-commands/{command_id}")
            if command["status"] == "pending":
                return None
            assert command["status"] == "completed", command
            return await self.journey.live.request("GET", f"/api/v1/environments/{environment['id']}")

        return await eventually(completed, bool, "Concurrent Environment command " + action)

    def events(self, environment, *, point=None):
        result = []
        for path in self.root.glob("events-*.jsonl"):
            for line in path.read_text().splitlines():
                event = json.loads(line)
                if event["environment_id"] == environment["id"] and (point is None or event["point"] == point):
                    result.append(event)
        return result

    def arm(self, point, *, action="pause", match=None, **facts):
        return arm(self.root / "faults", uuid4().hex, point=point, action=action, match={**(match or {}), **facts})

    async def reached(self, path):
        async def read():
            try:
                return json.loads((path / "hit-1.json").read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return None

        return await self.journey.live.wait(read, bool, "Environment lifecycle fault reached")

    @staticmethod
    def release(path):
        (path / "release").touch()

    def release_all(self):
        for path in self.root.glob("claims-paused-*"):
            path.unlink(missing_ok=True)
        for path in (self.root / "faults").glob("*/rule.json"):
            self.release(path.parent)


async def reset_workers(lab):
    for worker in lab.workers:
        if worker.returncode is None:
            lab.send(worker, signal.SIGCONT)
            await lab.stop(worker)
    lab.worker_environment["A13N_SERVICE_WORKER_CONCURRENCY"] = "1"
    lab.worker_environment.pop("LIVE_TEST_NO_REVERSE_ENVD", None)
    return await lab.start_worker()


async def add_second_worker(lab, *, no_reverse=False):
    options = {}
    if no_reverse:
        options["LIVE_TEST_NO_REVERSE_ENVD"] = "1"
    previous = dict(lab.worker_environment)
    try:
        lab.worker_environment.update(options)
        second = await lab.start_worker()
    finally:
        lab.worker_environment = previous
    first = next(worker for worker in reversed(lab.workers[:-1]) if worker.returncode is None)
    return WorkerPair(lab, [first, second])


def shell(script, *, wait=5):
    return {"tool": "shell_exec", "arguments": {"command": script, "cwd": ".", "yield_time_seconds": wait}}


async def native_file(backend, target, environment, path):
    if backend.kind == "docker":
        import docker

        row = await backend.journey.live.request("GET", f"/__live__/environments/{environment['id']}/lifecycle")

        def read():
            with docker.from_env() as client:
                container = client.containers.get(row["state"]["state"]["container_id"])
                result = container.exec_run(["cat", "/workspace/" + path])
                assert result.exit_code == 0, result.output
                return result.output

        return await asyncio.to_thread(read)
    if backend.kind != "e2b":
        return await asyncio.to_thread((target.root / path).read_bytes)
    from e2b import AsyncSandbox

    row = await backend.journey.live.request("GET", f"/__live__/environments/{environment['id']}/lifecycle")
    info = await AsyncSandbox.get_info(
        row["state"]["state"]["sandbox_id"], api_key=backend.settings.api_key.get_secret_value(), request_timeout=15
    )
    assert info.state.value == "running", "Native evidence must not resume a target stopped by another user"
    sandbox = await AsyncSandbox.connect(
        row["state"]["state"]["sandbox_id"], api_key=backend.settings.api_key.get_secret_value(), request_timeout=15
    )
    return bytes(await sandbox.files.read("/home/user/" + path, format="bytes"))
