"""Public HTTP journeys and read-only fault evidence for disposable Run tests."""

import json
import logging
import signal
from uuid import uuid4

from .client import agent_input
from .management_support import ManagementJourney
from .round_two_lab import private_json
from .run_faults import arm

logger = logging.getLogger(__name__)


class RunFaultJourney(ManagementJourney):
    async def setup(self):
        agent = await self.agent(
            plugins=[
                {
                    "instance_name": "faults",
                    "plugin_key": "live.run_faults",
                    "config": {"root": self.live.config["workspace_root"]},
                }
            ],
            retries={"tools": 1, "output": 1},
        )
        self.agent_id = agent["agent"]["id"]

    async def case(self, *, effect=False, idempotent=False, tool_failure="", **plan):
        case = await self.live.case("run_fault")
        if effect:
            plan["steps"] = [
                {
                    "tool": "live_fault_effect",
                    "arguments": {
                        "case_id": case["case_id"],
                        "token": case["token"],
                        "idempotent": idempotent,
                        "failure": tool_failure,
                    },
                }
            ]
        self.plan(case, **plan)
        return case

    async def start(self, case, **values):
        values.setdefault("agent_id", self.agent_id)
        return await super().start(case, **values)

    def arm(self, name, point, *, action="pause", role="worker", times=1, **match):
        return arm(self.lab.root / "faults", name, point=point, action=action, role=role, times=times, match=match)

    async def reached(self, path, *, hit=1):
        async def read():
            evidence = path / f"hit-{hit}.json"
            try:
                return json.loads(evidence.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return {}

        evidence = await self.live.wait(read, bool, f"fault {path.name}, hit {hit}")
        logger.info("fault evidence: %s", evidence)
        return evidence

    def release(self, path):
        (path / "release").touch()

    def effects(self, case):
        return sorted(
            path.read_text() for path in (self.lab.root / "workspace" / case["case_id"]).glob("business-effect-*")
        )

    async def state(self, run_id):
        return await self.live.request("GET", f"/__live__/faults/runs/{run_id}/state")

    async def execution(self, run_id):
        return await self.live.request("GET", f"/__live__/faults/runs/{run_id}/execution")

    async def inbox(self, thread_id):
        return (await self.live.request("GET", f"/__live__/threads/{thread_id}/inbox"))["items"]

    async def steer(self, run_id, token=None, key=None):
        token = token or uuid4().hex
        result = await self.post(
            f"/api/v1/runs/{run_id}/steer", agent_input("LIVE_STEER " + token), key=key, expected=202
        )
        return result, token

    async def queue(self, receipt, case, **values):
        thread = await self.live.thread(receipt["thread_id"])
        result = await self.post(
            f"/api/v1/threads/{thread['id']}/runs",
            {"expected_thread_version": thread["version"], "input": self.live.start_body(case)["input"], **values},
            expected=202,
        )
        assert result["outcome"] == "queued" and result["run"] is None
        return result["queued_submission"]

    async def restart_control(self):
        await self.lab.stop(self.lab.control, signal.SIGKILL)
        self.lab.control = await self.lab.spawn("dev.live_tests.manage", "control")
        await self.lab.ready(self.lab.control, self.live.config["control_url"])

    def save_config(self):
        private_json(self.lab.root / "config.json", self.lab.config)

    async def assert_settled(self, receipt, *, outcome="completed", effects=None, case=None):
        run = await self.live.finish(receipt["run_id"], outcome)
        attempts = await self.lab.attempts(run["id"])
        assert all(attempt["status"] in {"succeeded", "failed", "yielded", "cancelled"} for attempt in attempts)
        assert [attempt["attempt_number"] for attempt in attempts] == list(range(1, len(attempts) + 1))
        assert (await self.execution(run["id"]))["current_run_attempt_id"] is None
        if effects is not None:
            assert self.effects(case) == [case["token"]] * effects
        thread = await self.live.thread(run["thread_id"])
        if outcome == "completed":
            assert run["sealed_state_digest_sha256"] and thread["head_run_id"] == run["id"]
        return run, attempts

    def close_barriers(self):
        for path in (self.lab.root / "faults").glob("*/rule.json"):
            self.release(path.parent)
