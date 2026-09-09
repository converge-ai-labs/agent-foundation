"""Regression checks for startup readiness and bounded live-test failures."""

import asyncio
import json

import httpx2
import pytest
from botocore.exceptions import ClientError

from .client import LiveClient
from .local_storage import wait_ready


@pytest.mark.anyio
async def test_s3_readiness_retries_initial_service_unavailable():
    class StartingS3:
        calls = 0

        async def list_buckets(self):
            self.calls += 1
            if self.calls == 1:
                raise ClientError({"Error": {"Code": "503", "Message": "Service Unavailable"}}, "ListBuckets")
            return {"Buckets": []}

    client = StartingS3()
    async with asyncio.timeout(2):
        await wait_ready(client)
    assert client.calls == 2


@pytest.mark.anyio
async def test_missing_tool_evidence_fails_as_soon_as_run_ends():
    def response(request):
        if request.url.path == "/__live__/cases/example":
            return httpx2.Response(200, json={"tool_started": False})
        assert request.url.path == "/api/v1/runs/run_example"
        return httpx2.Response(200, json={"status": "completed", "failure": None})

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        live = LiveClient({"timeout_seconds": 120}, http)
        async with asyncio.timeout(1):
            with pytest.raises(AssertionError, match="Run ended before tool_started"):
                await live.wait_evidence({"case_id": "example"}, "tool_started", run_id="run_example")


@pytest.mark.anyio
async def test_tool_finishing_between_evidence_and_run_reads_is_not_a_failure():
    evidence_reads = 0

    def response(request):
        nonlocal evidence_reads
        if request.url.path == "/__live__/cases/example":
            evidence_reads += 1
            return httpx2.Response(200, json={"tool_started": evidence_reads > 1})
        return httpx2.Response(200, json={"status": "completed", "failure": None})

    async with httpx2.AsyncClient(base_url="http://127.0.0.1", transport=httpx2.MockTransport(response)) as http:
        live = LiveClient({}, http)
        evidence = await live.wait_evidence({"case_id": "example"}, "tool_started", run_id="run_example")
        assert evidence["tool_started"]


@pytest.mark.anyio
async def test_blocked_model_observes_peer_disconnect_without_releasing_gate(tmp_path):
    from starlette.requests import Request

    from .fixture_model import Case, _chunks

    async def receive():
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    request = Request({"type": "http"}, receive=receive)
    case = Case(case_id="a" * 32, scenario="interrupt_model", token="b" * 32)
    stream = _chunks(case, tmp_path, case.token, None, request=request)
    assert "assistant" in await anext(stream)
    async with asyncio.timeout(1):
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert (tmp_path / "model_started").is_file()
    assert (tmp_path / "model_closed").is_file()
    assert not (tmp_path / "release").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("content_parts", [False, True])
async def test_model_scenario_ignores_escaped_prompt_in_tool_result(tmp_path, delegated, content_parts):
    from fastapi import FastAPI

    from .fixture_model import Case, fixture_router

    app = FastAPI()
    app.include_router(fixture_router(tmp_path, lambda: None))
    case = Case(case_id="a" * 32, scenario="async_children" if delegated else "basic", token="b" * 32)
    prompt = "LIVE_TEST " + case.model_dump_json()
    content = json.dumps({"parent_task": prompt, "delegated_task": prompt + "\nLIVE_CHILD 0"}) if delegated else prompt
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://fixture") as http:
        created = await http.put(f"/__live__/cases/{case.case_id}", json=case.model_dump())
        assert created.status_code == 200
        await http.post(f"/__live__/cases/{case.case_id}/release")
        response = await http.post(
            "/__live__/model/v1/chat/completions",
            json={
                "stream": True,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": content}] if content_parts else content},
                    {"role": "tool", "content": json.dumps({"prompt": prompt + "\nLIVE_CHILD 0"})},
                ],
            },
        )
    assert response.status_code == 200
    assert "[DONE]" in response.text
    assert (tmp_path / case.case_id / "model_requests").read_text() == "request\n"
    if delegated:
        assert (tmp_path / case.case_id / "child_0_started").is_file()
        assert not (tmp_path / case.case_id / "parent_ready").exists()
