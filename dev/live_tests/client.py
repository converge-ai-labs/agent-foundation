"""Real HTTP operations, bounded waits, and cleanup of this test's Runs only."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic
from uuid import uuid4

import anyio
import httpx2

from .stream import Event, parse_events

logger = logging.getLogger(__name__)
ACTIVE = {"accepted", "running"}


def agent_input(text: str) -> dict:
    return {"schema_version": "2", "content": [{"type": "text", "text": text}]}


class LiveClient:
    def __init__(self, config: dict, http: httpx2.AsyncClient) -> None:
        self.config = config
        self.http = http
        self.timeout = float(config.get("timeout_seconds", 120))
        self.runs: list[str] = []
        self.cases: list[str] = []

    async def request(self, method: str, path: str, *, expected: int = 200, **kwargs) -> dict:
        response = await self.http.request(method, path, **kwargs)
        if response.status_code != expected:
            try:
                error = response.json().get("error", {})
                code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
            except ValueError:
                code = "non_json_response"
            raise AssertionError(f"{method} {path}: expected HTTP {expected}, got {response.status_code}; code={code}")
        return response.json()

    async def preflight(self) -> None:
        for role in ("control", "worker"):
            # Do not forward Control credentials to the Worker operational listener.
            async with httpx2.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as probe:
                response = await probe.get(f"{self.config[f'{role}_url']}/readyz")
            assert response.status_code == 200, f"Local {role} is not ready: HTTP {response.status_code}"
            assert response.json() == {"status": "ready", "role": role}, f"Expected a dedicated {role} process"
        assert self.config.get("agent_id"), "Run make live-test-setup to create API resources"
        await self.request("GET", f"/api/v1/workspaces/{self.config['workspace_id']}/agents/{self.config['agent_id']}")

    async def case(self, scenario: str) -> dict:
        case = {"case_id": uuid4().hex, "scenario": scenario, "token": uuid4().hex}
        await self.request("PUT", f"/__live__/cases/{case['case_id']}", json=case)
        self.cases.append(case["case_id"])
        return case

    async def evidence(self, case: dict) -> dict:
        return await self.request("GET", f"/__live__/cases/{case['case_id']}")

    async def release(self, case: dict) -> None:
        await self.request("POST", f"/__live__/cases/{case['case_id']}/release")

    async def wait_evidence(self, case: dict, field: str, *, run_id: str | None = None) -> dict:
        async def fetch():
            evidence = await self.evidence(case)
            if run_id and not evidence.get(field):
                run = await self.run(run_id)
                if run["status"] not in ACTIVE:
                    # The tool may have finished between the evidence and Run reads.
                    evidence = await self.evidence(case)
                    assert evidence.get(field), f"Run ended before {field}: {run_id}, {run['status']}, {run['failure']}"
            return evidence

        return await self.wait(fetch, lambda value: bool(value.get(field)), f"{field}: {case['case_id']}")

    async def start(self, case: dict, *, approval: bool = False, key: str | None = None) -> dict:
        body = self.start_body(case, approval=approval)
        receipt = await self.request(
            "POST",
            f"/api/v1/workspaces/{self.config['workspace_id']}/runs",
            expected=202,
            headers={"Idempotency-Key": key or uuid4().hex},
            json=body,
        )
        self.track(receipt)
        return receipt

    def start_body(self, case: dict, *, approval: bool = False) -> dict:
        body = {
            "agent_id": self.config["approval_agent_id" if approval else "agent_id"],
            "input": agent_input("LIVE_TEST " + json.dumps(case)),
        }
        if case["scenario"] in {"tools", "steer", "interrupt_tool", "stream"}:
            body["environment"] = {"environment_id": self.config["environment_id"]}
        return body

    def track(self, receipt: dict) -> None:
        if receipt["run_id"] not in self.runs:
            self.runs.append(receipt["run_id"])
        logger.info("run accepted: run=%s thread=%s", receipt["run_id"], receipt["thread_id"])

    async def run(self, run_id: str) -> dict:
        return await self.request("GET", f"/api/v1/runs/{run_id}")

    async def thread(self, thread_id: str) -> dict:
        return await self.request("GET", f"/api/v1/threads/{thread_id}")

    async def collection(self, path: str) -> list[dict]:
        items, seen, cursor = [], set(), None
        for _ in range(100):
            page = await self.request("GET", path, params={} if cursor is None else {"cursor": cursor})
            items.extend(page["items"])
            cursor = page.get("next_cursor")
            if cursor is None:
                return items
            assert cursor not in seen, "Collection cursor repeated"
            seen.add(cursor)
        raise AssertionError("Collection exceeded 100 pages")

    async def wait(self, fetch, predicate: Callable[[dict], bool], description: str) -> dict:
        deadline = monotonic() + self.timeout
        while monotonic() < deadline:
            value = await fetch()
            if predicate(value):
                return value
            await anyio.sleep(0.1)
        raise AssertionError(f"Timed out after {self.timeout}s: {description}")

    async def assert_stable(self, fetch, expected, *, seconds: float) -> None:
        deadline = monotonic() + seconds
        while True:
            assert await fetch() == expected, "An observation changed during the stability window"
            if monotonic() >= deadline:
                return
            await anyio.sleep(0.1)

    async def finish(self, run_id: str, outcome: str = "completed") -> dict:
        run = await self.wait(lambda: self.run(run_id), lambda run: run["status"] not in ACTIVE, run_id)
        failure = run.get("failure") or {}
        assert run["status"] == outcome, f"{run_id}: expected {outcome}, got {run['status']}; {failure.get('code')}"
        assert run["sealed_at"], "Settled Run has no seal"
        logger.info("run settled: run=%s status=%s", run_id, run["status"])
        return run

    async def interrupt(self, run_id: str) -> dict | None:
        # Read-version races are the only retries; transport failures are never replayed with a new key.
        for _ in range(10):
            run = await self.run(run_id)
            if run["status"] not in ACTIVE:
                return None
            thread = await self.thread(run["thread_id"])
            response = await self.http.post(
                f"/api/v1/runs/{run_id}/interrupt",
                headers={"Idempotency-Key": uuid4().hex},
                json={"expected_run_version": run["version"], "expected_thread_version": thread["version"]},
            )
            if response.status_code == 202:
                return response.json()
            if response.status_code not in {409, 412}:
                raise AssertionError(f"Interrupt {run_id} failed: HTTP {response.status_code}")
            await anyio.sleep(0.1)
        raise AssertionError(f"Interrupt {run_id} never obtained current versions")

    @asynccontextmanager
    async def stream(self, run_id: str, *, after: str | None = None) -> AsyncIterator[AsyncIterator[Event]]:
        headers = {"Accept": "text/event-stream"}
        if after is not None:
            headers["Last-Event-ID"] = after
        with anyio.fail_after(self.timeout):
            async with self.http.stream("GET", f"/api/v1/runs/{run_id}/stream", headers=headers) as response:
                assert response.status_code == 200, f"Stream {run_id}: HTTP {response.status_code}"
                assert response.headers.get("content-type", "").startswith("text/event-stream")
                yield parse_events(response.aiter_lines())

    async def events(self, run_id: str, *, after: str | None = None) -> list[Event]:
        events = []
        async with self.stream(run_id, after=after) as stream:
            async for event in stream:
                events.append(event)
                assert len(events) <= 10000, "Unexpectedly large live-test stream"
        return events

    async def cleanup(self) -> None:
        errors = []
        for run_id in reversed(self.runs):
            try:
                await self.interrupt(run_id)
            except Exception as error:
                errors.append(f"{run_id}: {type(error).__name__}")
        for case_id in self.cases:
            try:
                await self.release({"case_id": case_id})
            except Exception as error:
                errors.append(f"{case_id}: {type(error).__name__}")
        assert not errors, "Live-test cleanup failed: " + "; ".join(errors)
