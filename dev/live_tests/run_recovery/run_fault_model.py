"""Deterministic transport failures and observed input for Run fault journeys."""

import json
import re

import anyio
from fastapi.responses import JSONResponse, StreamingResponse

from ..infrastructure.round_two_model import tool_call
from ..infrastructure.run_faults import Faults

SCENARIOS = {"run_fault"}


async def completion(case, path, body):
    from ..infrastructure.fixture_model import _chunks, _message_text

    plan = json.loads(await anyio.Path(path / "plan.json").read_text())
    observations = anyio.Path(path / "observations.jsonl")
    async with await observations.open("a") as output:
        await output.write(json.dumps({"body": body}) + "\n")
    number = len((await observations.read_text()).splitlines())
    start = max(
        (
            index
            for index, message in enumerate(body["messages"])
            if message.get("role") == "user" and case.case_id in _message_text(message)
        ),
        default=-1,
    )
    # A queued successor inherits history. Earlier cases must not advance this
    # case's plan or supply its final answer.
    messages = body["messages"][start + 1 :]
    tool_results = [message for message in messages if message.get("role") == "tool"]
    await Faults(path.parent.parent / "faults", "control").reach(
        "model.request", case_id=case.case_id, request=number, tool_results=len(tool_results)
    )
    if number <= plan.get("failures", 0):
        mode = plan["failure"]
        if mode in {"401", "429", "503"}:
            return JSONResponse(
                {"error": {"message": "Injected upstream failure", "type": "live_fault"}},
                status_code=int(mode),
                headers={"Retry-After": "0"},
            )
        if mode == "timeout":
            await anyio.sleep(plan.get("delay_seconds", 2))
        elif mode in {"truncated", "malformed", "usage_truncated", "usage_timeout", "paused"}:

            async def broken():
                if mode == "malformed":
                    yield "data: {invalid-model-json\n\n"
                elif mode in {"usage_truncated", "usage_timeout", "paused"}:
                    async for chunk in usage_chunks(case, path, "PARTIAL_MUST_NOT_COMPLETE", None, plan):
                        if '"finish_reason": "stop"' in chunk:
                            continue
                        yield chunk
                        if '"usage"' in chunk or (mode == "paused" and '"content"' in chunk):
                            break
                    if mode == "paused":
                        await Faults(path.parent.parent / "faults", "control").reach(
                            "model.stream_paused", case_id=case.case_id
                        )
                    if mode == "usage_timeout":
                        await anyio.sleep(plan.get("delay_seconds", 10))
                    raise RuntimeError("Injected disconnect after upstream usage")
                else:
                    async for chunk in _chunks(case, path, "PARTIAL_MUST_NOT_COMPLETE", None):
                        yield chunk
                        if '"content"' in chunk:
                            break
                    raise RuntimeError("Injected upstream stream disconnect")

            return StreamingResponse(broken(), media_type="text/event-stream")
    steps = plan.get("steps", [])
    tool = None
    if "batches" in plan:
        # Count actual tool responses, including denied/no-response feedback.
        # A recovered request repeats a batch only when its results are absent.
        completed = 0
        for batch in plan["batches"]:
            if len(tool_results) == completed:
                tool = [tool_call(body, step["tool"], step["arguments"]) for step in batch]
                for index, call in enumerate(tool):
                    call["index"] = index
                break
            completed += len(batch)
    elif len(tool_results) < len(steps):
        step = steps[len(tool_results)]
        tool = tool_call(body, step["tool"], step["arguments"])
    steers = [
        token
        for message in messages
        if message.get("role") == "user"
        for token in re.findall(r"LIVE_STEER ([a-f0-9]{32})", _message_text(message))
    ]
    if steers:
        answer = "STEERS:" + ",".join(steers)
    else:
        answer = json.dumps(tool_results[-1]["content"]) if tool_results else case.token
    if plan.get("inline_child"):
        child = any(
            message.get("role") == "user" and "LIVE_INLINE_CHILD" in _message_text(message)
            for message in body["messages"]
        )
        if child:
            answer = "CHILD_" + case.token
        elif not tool_results:
            tool = tool_call(
                body,
                "delegate",
                {"subagent": "child", "prompt": "LIVE_TEST " + case.model_dump_json() + "\nLIVE_INLINE_CHILD"},
            )
    return StreamingResponse(usage_chunks(case, path, answer, tool, plan), media_type="text/event-stream")


async def usage_chunks(case, path, answer, tool, plan):
    from ..infrastructure.fixture_model import _chunks

    async for chunk in _chunks(case, path, answer, tool):
        if '"usage"' in chunk:
            if "usage" in plan:
                value = json.loads(chunk.removeprefix("data: "))
                value["usage"] = plan["usage"]
                chunk = "data: " + json.dumps(value) + "\n\n"
        yield chunk
