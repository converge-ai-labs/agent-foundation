"""Deterministic failures and barriers at the real HTTP model boundary."""

import json
from uuid import uuid4

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

SCENARIOS = {"model_error", "model_timeout", "tool_error", "checkpoint", "gate", "async_children", "protocol_tools"}
FLAGS = ("checkpoint_ready", "gate_ready", "child_0_started", "child_1_started", "parent_ready")
COUNTERS = ("effects", "effect_attempts", "checkpoint_requests", "injected_errors", "model_timeouts")


def matches_tool_name(value: str, name: str) -> bool:
    from a13n_service.connectivity.toolsets import portable_tool_name

    return any(value == alias or value.endswith("_" + alias) for alias in (name, portable_tool_name(name)))


def tool_call(body: dict, name: str, arguments: dict) -> dict:
    names = [entry["function"]["name"] for entry in body.get("tools", [])]
    selected = next((value for value in names if matches_tool_name(value, name)), None)
    if selected is None:
        raise HTTPException(400, f"Required live-test tool is not exposed: {name}")
    return {
        "index": 0,
        "id": "call_" + uuid4().hex,
        "type": "function",
        "function": {"name": selected, "arguments": json.dumps(arguments)},
    }


async def count(path, name: str):
    async with await anyio.Path(path / name).open("a") as output:
        await output.write("observed\n")


async def gate(path, name: str):
    await anyio.Path(path / name).touch()
    with anyio.fail_after(180):
        while not await anyio.Path(path / "release").exists():
            await anyio.sleep(0.05)


async def completion(case, path, body: dict, texts: list[str], tool_messages: list[dict]):
    from .fixture_model import _chunks

    tool, answer = None, case.token
    if case.scenario == "model_error" and not await anyio.Path(path / "release").exists():
        await count(path, "injected_errors")
        raise HTTPException(401, "live_test_invalid_model_credential")
    if case.scenario == "model_timeout":
        await count(path, "model_timeouts")
        # Wait before response headers so the configured model read timeout is exercised.
        await anyio.sleep(3)
    if case.scenario == "protocol_tools" and not tool_messages:
        tool = [tool_call(body, "live_effect", {"case_id": case.case_id, "token": case.token}) for _ in range(2)]
        for index, call in enumerate(tool):
            call["index"] = index
    elif case.scenario == "tool_error" or (case.scenario == "checkpoint" and not tool_messages):
        tool = tool_call(
            body, "live_effect", {"case_id": case.case_id, "token": case.token, "fail": case.scenario == "tool_error"}
        )
    elif case.scenario == "checkpoint":
        assert any(case.token in str(message.get("content")) for message in tool_messages)
        await count(path, "checkpoint_requests")
        await gate(path, "checkpoint_ready")
    elif case.scenario == "gate":
        await gate(path, "gate_ready")
    elif case.scenario == "async_children":
        from ..control.async_children_model import response

        tool, answer = await response(case, path, body, texts, tool_messages)
    if not body.get("stream"):
        raise HTTPException(400, "Live journeys require streamed model requests")
    return StreamingResponse(_chunks(case, path, answer, tool), media_type="text/event-stream")
