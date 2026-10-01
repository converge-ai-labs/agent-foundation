from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicyError


@pytest.fixture
def smoke(monkeypatch):
    path = Path(__file__).parents[2] / "provider-smoke" / "openrouter.py"
    spec = importlib.util.spec_from_file_location("openrouter_smoke", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    args = argparse.Namespace(
        command="call", model="vendor/model", settings='{"max_tokens": 256}', prompt="Reply with OK."
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer dummy-key"
        if request.method == "GET":
            assert request.url.path == "/api/v1/models"
            return httpx2.Response(200, json={"data": [{"id": "vendor/model", "name": "Example"}]})
        assert request.url.path == "/api/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == args.model
        assert body["max_tokens"] == 256
        assert body["messages"] == [{"role": "user", "content": args.prompt}]
        return httpx2.Response(
            200,
            json={
                "id": "reply",
                "object": "chat.completion",
                "created": 1,
                "model": args.model,
                "provider": "vendor",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            },
        )

    def run():
        async def execute():
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
                await module.run(args, "dummy-key", client)

        asyncio.run(execute())

    return args, calls, run


@pytest.mark.parametrize("command", ["test", "describe"])
def test_preview_uses_connection_probe_or_local_settings(smoke, capsys, command):
    args, calls, run = smoke
    args.command = command
    run()
    assert [call.method for call in calls] == (["GET"] if command == "test" else [])
    output = capsys.readouterr().out
    if command == "test":
        assert "Connection probe succeeded" in output
        assert "vendor/model" not in output
    else:
        assert "vendor/model" in output
    assert "dummy-key" not in output
    if command == "describe":
        assert "Available settings:" in output and "max_tokens" in output


def test_description_of_manual_model_keeps_local_schema(smoke, capsys):
    args, calls, run = smoke
    args.command = "describe"
    args.model = "vendor/unlisted"
    run()
    assert calls == []
    output = capsys.readouterr().out
    assert "vendor/unlisted" in output and "Available settings:" in output


@pytest.mark.parametrize("command", ["call", "walkthrough"])
def test_inference_uses_async_native_factory(smoke, monkeypatch, capsys, command):
    args, calls, run = smoke
    args.command = command
    monkeypatch.setattr("builtins.input", lambda _: "")
    run()
    assert [call.method for call in calls] == (["POST"] if command == "call" else ["GET", "POST"])
    output = capsys.readouterr().out
    assert '"text": "OK"' in output
    assert "dummy-key" not in output


def test_inference_rejects_invalid_endpoint_before_dispatch(smoke, monkeypatch):
    _, calls, run = smoke
    module = sys.modules["openrouter_smoke"]
    definition = module.DEFINITION
    monkeypatch.setattr(module, "DEFINITION", replace(definition, endpoint=f"{definition.endpoint}#fragment"))
    with pytest.raises(EndpointPolicyError):
        run()
    assert calls == []
