"""Seed verification keeps retained coverage while batching independent reads."""

from contextlib import nullcontext
from types import SimpleNamespace

import anyio
import httpx2
import pytest

from dev.service.seed_verify import _parallel, verify


def test_verify_batches_runs_and_reads_every_transcript_page():
    outcomes = {
        "failed_run": "failed",
        "failed_retry": "failed",
        "waiting_for_client": "waiting",
        "feedback_source": "waiting",
        "feedback_completed": "completed",
        "interrupted_run": "cancelled",
        "interrupted_with_queue": "cancelled",
        "interrupted_retry_completed": "completed",
        "structured_output": "completed",
        "malformed_output_failure": "failed",
        "follow_up_1": "completed",
        "follow_up_2": "completed",
        "follow_up_3": "completed",
    }
    runs = [
        {
            "id": name,
            "status": status,
            "sealed_at": "now",
            "thread_id": "thread",
            "session_id": "session",
            "agent_id": "agent",
            "agent_revision_id": "old",
            "retry_of_run_id": "failed_run",
            "parent_run_id": "feedback_source",
        }
        for name, status in outcomes.items()
    ]
    pages = []

    class Client:
        def __init__(self):
            self.http = SimpleNamespace(get=self.get)

        async def get(self, path, *, params):
            assert path.endswith("/items")
            return httpx2.Response(200, json={"items": [{"kind": "text"}], "next_cursor": "second"})

        async def collection(self, path, *, params=None):
            if path.endswith("/items"):
                assert params["cursor"] == "second"
                pages.append(path)
                return [{"kind": "tool_call"}]
            if path.startswith("/api/v1/workspaces/empty/"):
                return []
            if path.endswith(("/agents", "/skills", "/assets")):
                return [{"id": str(index)} for index in range(51)]
            if path.endswith("/web-providers"):
                return [
                    {"id": kind, "type": kind, "enabled": True, "credential_configured": True}
                    for kind in ("brave", "exa")
                ]
            if path.endswith("/sessions"):
                return [{"id": "session"}]
            if path == "/api/v1/sessions/session/threads":
                return [{"id": "thread", "session_id": "session", "origin_kind": "new"}]
            if path == "/api/v1/workspaces/workspace/runs":
                return runs
            assert not path.endswith("/runs"), "Run discovery must use the Workspace collection"
            return [{"id": "resource"}]

        async def request(self, method, path):
            if path.endswith("/agents/agent"):
                return {"current_revision_id": "new"}
            return {"expires_at": "2020-01-01T00:00:00+00:00"}

        def scope(self, workspace_id):
            return nullcontext()

    async def check():
        coverage = await verify(
            Client(),
            {
                "workspace_id": "workspace",
                "empty_workspace_id": "empty",
                "session_count": 1,
                "long_thread_id": "thread",
                "agent_ids": ["agent"],
                "scenarios": {
                    "conversations": {name: name for name in outcomes},
                    "identity": {"api_key_expired": "key"},
                    "resources": {"web_provider_brave": "brave", "web_provider_exa": "exa"},
                },
            },
        )
        assert coverage["run_count"] == 13
        assert coverage["retained_item_kinds"] == {"text": 13, "tool_call": 13}
        assert len(pages) == 13

    anyio.run(check)


def test_parallel_verification_is_bounded_and_cancels_siblings_on_failure():
    async def check():
        active = 0
        peak = 0
        filled = anyio.Event()

        async def read(index):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 8:
                filled.set()
            try:
                await filled.wait()
                if index == 0:
                    raise RuntimeError("verification failed")
                await anyio.sleep_forever()
            finally:
                active -= 1

        with anyio.fail_after(2), pytest.raises(ExceptionGroup, match="TaskGroup"):
            await _parallel(list(range(30)), read)
        assert active == 0 and peak == 8

    anyio.run(check)
