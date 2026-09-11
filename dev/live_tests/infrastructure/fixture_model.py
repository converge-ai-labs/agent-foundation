"""A deterministic HTTP model fixture; Foundation still owns all Agent execution.

This fixture implements just the OpenAI Chat Completions scenarios used below.
It does not validate real-provider inference quality or provider compatibility.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import time
from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..harness_integration import management_model
from ..run_recovery import run_fault_model
from . import round_two_model

CASE_ID = r"^[a-f0-9]{32}$"
SCENARIOS = (
    {
        "basic",
        "protocol_text",
        "remember",
        "tools",
        "stream",
        "steer",
        "interrupt_model",
        "interrupt_tool",
        "approval",
    }
    | round_two_model.SCENARIOS
    | management_model.SCENARIOS
    | run_fault_model.SCENARIOS
)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=CASE_ID)
    scenario: str
    token: str = Field(pattern=CASE_ID)


def fixture_router(root: Path, authenticate, *, long_session=None) -> APIRouter:
    router = APIRouter(prefix="/__live__", dependencies=[Depends(authenticate)])

    def case_path(case_id: str) -> Path:
        if not re.fullmatch(CASE_ID, case_id):
            raise HTTPException(400, "Invalid case identity")
        return root / case_id

    @router.put("/cases/{case_id}")
    async def create_case(case_id: str, body: Case):
        if body.case_id != case_id or body.scenario not in SCENARIOS:
            raise HTTPException(400, "Invalid live-test case")
        path = anyio.Path(case_path(case_id))
        try:
            await path.mkdir(mode=0o700)
        except FileExistsError as error:
            raise HTTPException(409, "Case already exists") from error
        await (path / "case.json").write_text(body.model_dump_json())
        return {"case_id": case_id}

    @router.get("/cases/{case_id}")
    async def evidence(case_id: str):
        path = anyio.Path(case_path(case_id))
        if not await (path / "case.json").is_file():
            raise HTTPException(404, "Case not found")
        result = {}
        for name in (
            "model_started",
            "model_closed",
            "tool_started",
            "tool_closed",
            "management_ready",
            *round_two_model.FLAGS,
        ):
            result[name] = await (path / name).is_file()
        for name in ("output", "shell_read"):
            result[name] = await (path / name).read_text() if await (path / name).is_file() else ""
        for name in ("model_requests", "approval_executions", *round_two_model.COUNTERS):
            result[name] = len((await (path / name).read_text()).splitlines()) if await (path / name).is_file() else 0
        steers = path / "steer_observations"
        result["steer_observations"] = (
            [json.loads(line) for line in (await steers.read_text()).splitlines()] if await steers.is_file() else []
        )
        deliveries = path / "result_deliveries"
        result["result_deliveries"] = (
            [json.loads(line) for line in (await deliveries.read_text()).splitlines()]
            if await deliveries.is_file()
            else []
        )
        return result

    @router.post("/cases/{case_id}/release")
    async def release(case_id: str):
        path = anyio.Path(case_path(case_id))
        if not await (path / "case.json").is_file():
            raise HTTPException(404, "Case not found")
        await (path / "release").touch()
        return {"released": True}

    @router.post("/model/v1/chat/completions")
    @router.post("/model-alternate/v1/chat/completions")
    async def completion(request: Request):
        body = await request.json()
        texts = [_message_text(message) for message in body["messages"]]
        matches = [match for text in texts for match in re.findall(r"^LIVE_TEST (\{[^\n]+\})$", text, re.MULTILINE)]
        if not matches:
            raise HTTPException(400, "Expected a LIVE_TEST scenario in the model context")
        case = Case.model_validate_json(matches[-1])
        path = case_path(case.case_id)
        stored = Case.model_validate_json(await anyio.Path(path / "case.json").read_text())
        if case != stored:
            raise HTTPException(400, "Scenario does not match its test-owned case")
        async with await anyio.open_file(path / "model_requests", "a") as output:
            await output.write("request\n")
        if long_session is not None:
            from ..performance.long_session_model import completion

            return await completion(case, path, body, long_session)
        tool_messages = [message for message in body["messages"] if message.get("role") == "tool"]
        if case.scenario in run_fault_model.SCENARIOS:
            return await run_fault_model.completion(case, path, body)
        if case.scenario in management_model.SCENARIOS:
            return await management_model.completion(case, path, body, request)
        if case.scenario in round_two_model.SCENARIOS:
            return await round_two_model.completion(case, path, body, texts, tool_messages)
        if case.scenario == "steer":
            steers = [value for text in texts for value in re.findall(r"LIVE_STEER ([a-f0-9]{32})", text)]
            async with await anyio.open_file(path / "steer_observations", "a") as output:
                await output.write(json.dumps(steers) + "\n")
        tool = None
        answer = case.token
        if case.scenario == "remember":
            answer = case.token if any("Recall the remembered token." in text for text in texts) else "remembered"
        elif case.scenario in {"tools", "stream", "steer", "interrupt_tool", "approval"} and not tool_messages:
            name = "live_approved_write" if case.scenario == "approval" else "shell_exec"
            names = [entry["function"]["name"] for entry in body.get("tools", [])]
            selected = next((value for value in names if value == name or value.endswith("_" + name)), None)
            if selected is None:
                raise HTTPException(400, f"Required live-test tool is not exposed: {name}")
            arguments = (
                {"case_id": case.case_id, "token": case.token}
                if case.scenario == "approval"
                else {
                    "command": _shell_command(path, case),
                    "yield_time_seconds": 120,
                    "execution_timeout_seconds": 150,
                }
            )
            tool = {
                "index": 0,
                "id": "call_" + uuid4().hex,
                "type": "function",
                "function": {
                    "name": selected,
                    "arguments": json.dumps(arguments),
                },
            }
        elif case.scenario == "steer":
            answer = steers[0] if len(steers) == 1 else f"steer-count:{len(steers)}"
        elif case.scenario == "approval":
            answer = "approval-resolved"
        if not body.get("stream"):
            raise HTTPException(400, "Live journeys require streamed model requests")
        return StreamingResponse(_chunks(case, path, answer, tool, request=request), media_type="text/event-stream")

    return router


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    if isinstance(content, str):
        if message.get("role") == "user":
            try:
                task = json.loads(content)
            except ValueError:
                task = None
            if isinstance(task, dict) and isinstance(task.get("delegated_task"), str):
                return "\n".join(
                    task[key] for key in ("parent_task", "delegated_task") if isinstance(task.get(key), str)
                )
        return content
    return ""


async def _chunks(
    case: Case, path: Path, answer: str, tool: dict | list[dict] | None, *, request: Request | None = None
):
    identifier, created = "chatcmpl_" + uuid4().hex, int(time.time())

    def frame(delta: dict, finish=None, *, usage=False):
        value = {"id": identifier, "object": "chat.completion.chunk", "created": created, "model": "live-fixture"}
        value["choices"] = [] if usage else [{"index": 0, "delta": delta, "finish_reason": finish}]
        if usage:
            value["usage"] = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        return "data: " + json.dumps(value) + "\n\n"

    yield frame({"role": "assistant"})
    if case.scenario == "interrupt_model":
        await anyio.Path(path / "model_started").touch()
        try:
            with anyio.fail_after(150):
                while not await anyio.Path(path / "release").exists():
                    # ASGI 2.4 detects disconnects on send; this gate deliberately sends no frames.
                    if request is None:
                        await anyio.sleep(0.05)
                    else:
                        # Middleware may checkpoint before delivering receive(); an already-cancelled
                        # is_disconnected() poll cannot observe that message.
                        with anyio.move_on_after(0.05):
                            if (await request.receive())["type"] == "http.disconnect":
                                return
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.Path(path / "model_closed").touch()
    if case.scenario == "protocol_text":
        yield frame({"reasoning_content": "PRIVATE_PROTOCOL_REASONING"})
        answer = "协议🙂\n" + case.token
    if tool is not None:
        yield frame({"tool_calls": tool if isinstance(tool, list) else [tool]})
        yield frame({}, "tool_calls")
    else:
        for offset in range(0, len(answer), 8):
            yield frame({"content": answer[offset : offset + 8]})
            await anyio.sleep(0.01)
        yield frame({}, "stop")
    yield frame({}, usage=True)
    yield "data: [DONE]\n\n"


def _shell_command(path: Path, case: Case) -> str:
    code = (
        "from pathlib import Path\nimport signal, time\n"
        f"root = Path({str(path)!r})\n"
        "def stop(signum, frame):\n    raise SystemExit(128 + signum)\n"
        "signal.signal(signal.SIGTERM, stop)\nsignal.signal(signal.SIGINT, stop)\n"
        "try:\n    (root / 'tool_started').touch()\n"
    )
    if case.scenario != "tools":
        code += (
            "    deadline = time.monotonic() + 140\n"
            "    while not (root / 'release').exists():\n"
            "        if time.monotonic() > deadline: raise TimeoutError('live tool gate expired')\n"
            "        time.sleep(0.05)\n"
        )
    code += (
        f"    (root / 'output').write_text({case.token!r})\n"
        "    (root / 'shell_read').write_text((root / 'output').read_text())\n"
        "    print((root / 'shell_read').read_text())\n"
        "finally:\n    (root / 'tool_closed').touch()\n"
    )
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)
