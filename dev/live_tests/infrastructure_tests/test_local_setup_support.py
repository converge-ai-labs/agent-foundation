"""Regression checks for startup readiness and bounded live-test failures."""

import asyncio
import json
from contextlib import AsyncExitStack
from types import SimpleNamespace

import httpx2
import pytest
from botocore.exceptions import ClientError

from ..infrastructure.client import LiveClient
from ..infrastructure.local_storage import wait_ready


@pytest.mark.anyio
@pytest.mark.parametrize("failed_attempts", [0, 1, 3])
async def test_owned_storage_replaces_only_missing_mappings_and_cleans_every_attempt(
    monkeypatch, caplog, failed_attempts
):
    from testcontainers.core import container as containers

    from ..infrastructure import local_storage

    events = []
    created = []

    class Container:
        def __init__(self, image):
            self.number = len(created) + 1
            created.append(self)
            self.native = SimpleNamespace(
                id=f"owned-{self.number}", status="running", attrs={"NetworkSettings": {"Ports": {"9000/tcp": []}}}
            )
            self.native.reload = self.reload

        def with_env(self, *args):
            return self

        def with_command(self, *args):
            return self

        def with_bind_ports(self, *args):
            assert args == (9000, ("127.0.0.1", 0))
            return self

        def start(self):
            events.append(("start", self.number))

        def stop(self):
            events.append(("stop", self.number))

        def get_wrapped_container(self):
            return self.native

        def reload(self):
            if self.number > failed_attempts:
                self.native.attrs["NetworkSettings"]["Ports"]["9000/tcp"] = [
                    {"HostIp": "127.0.0.1", "HostPort": "55020"}
                ]

    monkeypatch.setattr(containers, "DockerContainer", Container)
    monkeypatch.setattr(local_storage, "PORT_MAPPING_TIMEOUT_SECONDS", 0.02)
    async with AsyncExitStack() as stack:
        if failed_attempts == 3:
            with pytest.raises(RuntimeError, match="bounded startup retries"):
                await local_storage.start_rustfs(stack, "private-access", "private-secret")
        else:
            assert (
                await local_storage.start_rustfs(stack, "private-access", "private-secret") == "http://127.0.0.1:55020"
            )
            assert events[-1] == ("start", failed_attempts + 1)
    count = min(failed_attempts + 1, 3)
    assert events == [event for number in range(1, count + 1) for event in [("start", number), ("stop", number)]]
    assert len(created) == count
    assert "private-access" not in caplog.text and "private-secret" not in caplog.text


@pytest.mark.anyio
async def test_published_port_waits_for_delayed_mapping_without_restarting(monkeypatch):
    from ..infrastructure import local_storage

    native = SimpleNamespace(id="owned", status="running", attrs={}, calls=0)

    def reload():
        native.calls += 1
        if native.calls == 2:
            native.attrs = {"NetworkSettings": {"Ports": {"9000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55021"}]}}}

    native.reload = reload
    container = SimpleNamespace(get_wrapped_container=lambda: native)
    monkeypatch.setattr(local_storage, "PORT_MAPPING_TIMEOUT_SECONDS", 1)
    assert await local_storage.wait_published_port(container) == 55021
    assert native.calls == 2


@pytest.mark.anyio
async def test_exited_storage_is_not_treated_as_a_retryable_mapping_collision():
    from ..infrastructure.local_storage import PortMappingUnavailable, wait_published_port

    native = SimpleNamespace(id="owned", status="exited", attrs={}, reload=lambda: None)
    with pytest.raises(RuntimeError, match="exited before publishing") as caught:
        await wait_published_port(SimpleNamespace(get_wrapped_container=lambda: native))
    assert not isinstance(caught.value, PortMappingUnavailable)


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

    from ..infrastructure.fixture_model import Case, _chunks

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

    from ..infrastructure.fixture_model import Case, fixture_router

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
