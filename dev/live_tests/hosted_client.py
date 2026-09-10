"""Real Hosted AG-UI HTTP consumption; no in-process gateway or projection calls."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

import anyio

from .stream import parse_frames
from .stream_contract import AGUI


def message_snapshot(frames):
    """A client transcript assembled from standard wire IDs, roles, and deltas."""
    messages, calls = {}, {}
    for frame in frames:
        event = frame.data
        kind = event["type"]
        if kind == "TEXT_MESSAGE_START":
            messages[event["messageId"]] = {"id": event["messageId"], "role": event["role"], "content": ""}
        elif kind == "TEXT_MESSAGE_CONTENT":
            messages[event["messageId"]]["content"] += event["delta"]
        elif kind == "TOOL_CALL_START":
            parent = event.get("parentMessageId")
            assert parent, "The tool fixture must expose its parent message identity for transcript replay"
            message = messages.setdefault(parent, {"id": parent, "role": "assistant"})
            call = {
                "id": event["toolCallId"],
                "type": "function",
                "function": {"name": event["toolCallName"], "arguments": ""},
            }
            message.setdefault("toolCalls", []).append(call)
            calls[event["toolCallId"]] = call
        elif kind == "TOOL_CALL_ARGS":
            calls[event["toolCallId"]]["function"]["arguments"] += event["delta"]
        elif kind == "TOOL_CALL_RESULT":
            messages[event["messageId"]] = {
                "id": event["messageId"],
                "role": "tool",
                "toolCallId": event["toolCallId"],
                "content": event["content"],
            }
    return list(messages.values())


class HostedClient:
    def __init__(self, live, case, *, agent_id=None, parent=None):
        self.live, self.case = live, case
        self.agent_id = agent_id or live.config["agent_id"]
        self.parent = parent
        self.native_id = None
        self.body = {
            "threadId": "client-thread-" + uuid4().hex,
            "runId": "client-run-" + uuid4().hex,
            "messages": [{"id": uuid4().hex, "role": "user", "content": "LIVE_TEST " + json.dumps(case)}],
            "state": {},
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }

    @property
    def path(self):
        return f"/ag-ui/v1/agents/{self.agent_id}/runs"

    async def run(self):
        if self.native_id is None:

            async def discover():
                runs = await self.live.collection(f"/api/v1/workspaces/{self.live.config['workspace_id']}/runs")
                matching = [
                    run
                    for run in runs
                    if run["agent_id"] == self.agent_id
                    and (
                        run.get("parent_run_id") == self.parent
                        if self.parent
                        else self.case["case_id"] in json.dumps(run["input"])
                    )
                ]
                assert len(matching) <= 1, "One hosted runId accepted multiple Native Runs"
                return matching[0] if matching else {}

            run = await self.live.wait(discover, bool, "Hosted Run acceptance in Native resources")
            self.native_id = run["id"]
            self.live.track({"run_id": run["id"], "thread_id": run["thread_id"]})
        return await self.live.run(self.native_id)

    @asynccontextmanager
    async def stream(self, *, after=None):
        headers = {"Accept": "text/event-stream"}
        if after is not None:
            headers["Last-Event-ID"] = after
        with anyio.fail_after(self.live.timeout):
            async with self.live.http.stream("POST", self.path, headers=headers, json=self.body) as response:
                if response.status_code != 200:
                    await response.aread()
                    try:
                        error = response.json().get("error")
                    except ValueError:
                        error = "non-JSON error response"
                    raise AssertionError(f"Hosted stream: HTTP {response.status_code}; {error}")
                assert response.headers.get("content-type", "").startswith("text/event-stream")
                # Acceptance is durable before SSE. Register cleanup even if parsing fails.
                await self.run()
                yield self._events(response)

    async def _events(self, response):
        count = 0
        async for event in parse_frames(response.aiter_lines()):
            count += 1
            assert count <= 10000
            AGUI.validate_python(event.data, strict=True)
            yield event

    async def events(self, *, after=None):
        async with self.stream(after=after) as events:
            return [event async for event in events]

    async def cancel(self):
        response = await self.live.http.post(
            f"/ag-ui/v1/agents/{self.agent_id}/cancel",
            json={"threadId": self.body["threadId"], "runId": self.body["runId"]},
        )
        assert response.is_success, f"Hosted cancellation: HTTP {response.status_code}"

    async def private_ids(self):
        run = await self.run()
        attempts = await self.live.collection(f"/api/v1/runs/{run['id']}/attempts")
        return [
            run["id"],
            run["thread_id"],
            *[value for attempt in attempts for value in (attempt["id"], attempt["harness_run_id"]) if value],
        ]
