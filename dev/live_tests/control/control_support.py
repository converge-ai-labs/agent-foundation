"""HTTP commands and model-context evidence shared by control transition journeys."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from ..infrastructure.client import agent_input
from ..infrastructure.management_support import client_tool
from ..run_recovery.run_fault_support import RunFaultJourney
from .contention_support import measured_call

logger = logging.getLogger(__name__)


class ControlJourney(RunFaultJourney):
    async def control_agent(self, **config):
        return await self.agent(
            client_tools=[client_tool()],
            plugins=[
                {"instance_name": name, "plugin_key": key, "config": {"root": self.live.config["workspace_root"]}}
                for name, key in (("faults", "live.run_faults"), ("approval", "live.approval"))
            ],
            **config,
        )

    async def waiting(self, *, kind="client_tool", rounds=1, mixed=False, source=None):
        agent = await self.control_agent() if source is None else None
        case = await self.case()
        batch = [{"tool": "live_client", "arguments": {"prompt": "Supply a value"}}]
        approval = {
            "tool": "live_approved_write",
            "arguments": {"case_id": case["case_id"], "token": case["token"]},
        }
        if kind == "approval":
            batch = [approval]
        if mixed:
            batch.append(approval)
        self.plan(case, batches=[batch for _ in range(rounds)])
        if source is None:
            receipt = await self.start(case, agent_id=agent["agent"]["id"])
        else:
            receipt = await self.accept(*await self.command(source, "continue", case=case))
        return case, await self.live.finish(receipt["run_id"], "waiting")

    async def command(self, source, operation, *, case=None, input=None, resolutions=None):
        run_id = source.get("id", source.get("run_id"))
        thread = await self.live.thread(source["thread_id"])
        body = {"expected_thread_version": thread["version"]}
        path = f"/api/v1/runs/{run_id}/{operation}"
        if operation in {"continue", "fork", "submit", "waiting_continue"}:
            body["input"] = input or (
                self.live.start_body(case)["input"] if case else agent_input("Continue this work.")
            )
        if operation == "fork":
            body.pop("expected_thread_version")
        if operation in {"submit", "waiting_continue"}:
            path = f"/api/v1/threads/{source['thread_id']}/runs"
        if operation == "feedback":
            body["sealed_state_digest_sha256"] = source["sealed_state_digest_sha256"]
            body["resolutions"] = resolutions or []
        if operation == "waiting_continue":
            body["waiting_resolution"] = {
                "mode": "defaults",
                "sealed_state_digest_sha256": source["sealed_state_digest_sha256"],
            }
        if operation == "interrupt":
            run = await self.live.run(run_id)
            body["expected_run_version"] = run["version"]
        if operation == "consume":
            path = f"/api/v1/threads/{source['thread_id']}/queued-submissions/consume"
            body["expected_queue_version"] = thread["queue_version"]
        return path, body

    async def accept(self, path, body, *, key=None):
        result = await self.post(path, body, key=key, expected=202)
        receipt = result.get("run") if "outcome" in result else result
        assert receipt and receipt["status"] == "accepted", result
        self.live.track(receipt)
        return receipt

    async def approve(self, waiting):
        pending = await self.live.request("GET", f"/api/v1/runs/{waiting['id']}/pending-actions")
        return [
            {
                "call_id": item["call_id"],
                **(
                    {"action": "approve"}
                    if item["kind"] == "approval"
                    else {"action": "complete", "result": {"value": uuid4().hex}}
                ),
            }
            for item in pending["items"]
        ]

    async def thread_runs(self, source):
        return await self.live.collection(f"/api/v1/threads/{source['thread_id']}/runs")

    async def queued(self, source):
        return await self.live.collection(f"/api/v1/threads/{source['thread_id']}/queued-submissions")

    async def queue_row(self, row):
        return await self.live.request("GET", f"/api/v1/queued-submissions/{row['queued_submission_id']}")

    async def consumed_queue(self, row):
        consumed = await self.live.wait(
            lambda: self.queue_row(row), lambda value: value["state"] != "queued", "queue outcome"
        )
        assert consumed["state"] == "consumed", consumed
        if consumed["consumed_run_id"] not in self.live.runs:
            self.live.runs.append(consumed["consumed_run_id"])
        return consumed

    async def finish_queue(self, row):
        consumed = await self.consumed_queue(row)
        return await self.live.finish(consumed["consumed_run_id"])

    @asynccontextmanager
    async def post_in_flight(self, path, body, *, key=None, metric_operation=None, expected_statuses=()):
        task = asyncio.create_task(
            measured_call(
                metric_operation,
                "Interrupt HTTP writer competing with worker queue handoff",
                lambda: self.live.http.post(path, json=body, headers={"Idempotency-Key": key or uuid4().hex}),
                expected_statuses=expected_statuses,
            )
        )
        try:
            yield task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def race(
        self, commands, *, point="control.state_published", metric_operation=None, expected_statuses=(409,), **match
    ):
        barrier = self.arm("race-" + uuid4().hex, point, role="control", times=len(commands), **match)
        tasks = [
            asyncio.create_task(
                measured_call(
                    metric_operation,
                    f"Concurrent admission HTTP writer {index + 1}/{len(commands)}",
                    lambda path=path, body=body, key=key: self.live.http.post(
                        path, json=body, headers={"Idempotency-Key": key}
                    ),
                    expected_statuses=expected_statuses,
                )
            )
            for index, (path, body, key) in enumerate(commands)
        ]
        try:
            hits = [await self.reached(barrier, hit=index) for index in range(1, len(tasks) + 1)]
            assert len({hit["run_id"] for hit in hits}) == len(tasks)
            self.release(barrier)
            responses = await asyncio.gather(*tasks)
            logger.info("Concurrent control replies: %s", [response.status_code for response in responses])
            return responses
        finally:
            self.release(barrier)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def user_texts(observation):
    return [
        json.dumps(message.get("content"), ensure_ascii=False)
        for message in observation["body"]["messages"]
        if message.get("role") == "user"
    ]


def assert_tokens_once(observation, tokens):
    text = "\n".join(user_texts(observation))
    for token in tokens:
        assert text.count(token) == 1, (token, text)


def assert_absent(observations, tokens):
    for observed in observations:
        text = "\n".join(user_texts(observed))
        assert all(token not in text for token in tokens), text
