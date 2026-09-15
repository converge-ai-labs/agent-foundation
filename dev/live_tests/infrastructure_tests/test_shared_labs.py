"""Sharing must keep per-case ownership and retire labs after failed journeys."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest

from ..infrastructure import shared_labs
from ..infrastructure.client import LiveClient


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, "test", "cleanup"])
async def test_shared_cases_keep_separate_ledgers_and_retire_failed_labs(monkeypatch, failure):
    opened, closed, released = [], [], []

    def response(request):
        if request.method == "POST":
            released.append(request.url.path)
            status = 500 if failure == "cleanup" and request.url.path.endswith("case-one/release") else 200
            return httpx2.Response(status, json={})
        return httpx2.Response(200, json={"status": "completed"})

    @asynccontextmanager
    async def open_lab(*, suite, smoke):
        assert smoke is True
        async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
            lab = SimpleNamespace(config={}, client=LiveClient({}, http), suite=suite)
            opened.append(lab)
            try:
                yield lab
            finally:
                closed.append(lab)

    monkeypatch.setattr(shared_labs, "open_lab", open_lab)
    pool = shared_labs.SharedLabs()
    request = SimpleNamespace(node=SimpleNamespace(live_call_report=SimpleNamespace(failed=failure == "test")))

    async def first():
        async with pool.case("core", request) as lab:
            lab.client.runs.append("run-one")
            lab.client.cases.append("case-one")

    try:
        if failure == "cleanup":
            with pytest.raises(AssertionError, match="cleanup failed"):
                await first()
        else:
            await first()
        request.node.live_call_report.failed = False
        async with pool.case("core", request) as lab:
            assert lab.client.runs == [] and lab.client.cases == []
            lab.client.cases.append("case-two")
        assert len(opened) == (2 if failure else 1)
        assert released == ["/__live__/cases/case-one/release", "/__live__/cases/case-two/release"]
        async with pool.case("management", request):
            assert len(closed) == len(opened) - 1, "Previous group's processes were retained"
    finally:
        await pool.close()
    assert closed == opened
