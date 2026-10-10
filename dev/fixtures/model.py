"""Local scripted OpenAI-compatible model for UI development, not real inference."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import anyio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import findings as finding_fixture
from .api import router as fixture_router
from .mem0 import keep as keep_mem0_records
from .mem0 import router as mem0_router
from .process import fixture_process

MODEL_PORT = 18080
MODEL_URL = f"http://127.0.0.1:{MODEL_PORT}/v1"
app = FastAPI()
app.include_router(fixture_router)
# A fake self-hosted mem0 server, which seeded record memories use.
app.include_router(mem0_router, prefix="/mem0")
app.state.request_count = 0
app.state.last_message_roles = ()
app.state.observations = []


@app.get("/healthz")
async def health():
    return {"status": "ok", "model": "local-scripted"}


@app.get("/fixture/model-state")
async def model_state():
    return {
        "request_count": app.state.request_count,
        "last_message_roles": app.state.last_message_roles,
        "observations": app.state.observations,
    }


@app.post("/v1/chat/completions")
async def completion(request: Request):
    observation = {"arrived_at": time.time()}
    app.state.observations.append(observation)
    del app.state.observations[:-64]
    body = await request.json()
    observation["body_read_at"] = time.time()
    messages = body.get("messages", [])
    app.state.request_count += 1
    app.state.last_message_roles = tuple(message.get("role") for message in messages[-256:])
    prompt = "\n".join(text_of(message) for message in messages if message.get("role") == "user")
    if "[fail]" in prompt:
        return JSONResponse(
            {"error": {"message": "Intentional local model failure", "type": "invalid_request_error"}}, status_code=400
        )
    if "[interruptible]" in prompt:
        await anyio.sleep(3)
    tool_result = next(
        (message.get("content") for message in reversed(messages) if message.get("role") == "tool"), None
    )
    tool_call = (
        planned_tool(body, prompt)
        if tool_result is None
        or finding_fixture.selection(prompt) is not None
        or any(flag in prompt for flag in ("[structured-invalid]", "[delegate]", "[workspace]", "[memory-edit]"))
        else None
    )
    tool_calls = tool_call if isinstance(tool_call, list) else [tool_call] if tool_call is not None else []
    observation["tool_call_selected"] = bool(tool_calls)
    document = Path(__file__).with_name("response.md").read_text()
    text = (
        document
        if "[long]" in prompt
        else (
            "This is a local scripted response for UI development.\n\n"
            "已收到你的消息。这里使用虚构内容，方便检查排版、交互与运行记录。\n\n" + document.split("\n\n", 1)[-1][:220]  # noqa: RUF001
        )
    )
    if "[steer-proof]" in prompt:
        text = "Revised answer: amber." if "[steer-update]" in prompt else "Initial answer: cobalt."
    if tool_result is not None:
        text = (
            "## Local tool result\n\nThe local fixture returned:\n\n```json\n" + str(tool_result) + "\n```\n\n" + text
        )
    for key, example in finding_fixture.EXAMPLES.items():
        if f"[finding-{key}]" in prompt:
            text = example["reply"]
    if finding_fixture.selection(prompt) is not None:
        text = "## Fictional Findings analysis\n\nRead the selected trace roots and steps, then submitted the recognized preview diagnoses with trace evidence. These scripted examples exercise review and navigation; they do not measure inference quality or establish complete review."
    response_id = "chatcmpl-local-development"
    usage = {"prompt_tokens": 20, "completion_tokens": len(text) // 4, "total_tokens": 20 + len(text) // 4}
    usage_chunk = (
        "data: "
        + json.dumps(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "local-scripted",
                "choices": [],
                "usage": usage,
            }
        )
        + "\n\n"
    )
    include_usage = body.get("stream_options", {}).get("include_usage", False)
    if not body.get("stream"):
        observation["response_prepared_at"] = time.time()
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "local-scripted",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": tool_calls}
                    if tool_call
                    else {"role": "assistant", "content": text},
                    "finish_reason": "tool_calls" if tool_call else "stop",
                }
            ],
            "usage": usage,
        }

    async def chunks():
        async def emit(delta, finish=None):
            return (
                "data: "
                + json.dumps(
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "local-scripted",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

        yield await emit({"role": "assistant", "content": ""})
        if tool_call:
            yield await emit({"tool_calls": [{"index": index, **item} for index, item in enumerate(tool_calls)]})
            yield await emit({}, "tool_calls")
            if include_usage:
                yield usage_chunk
            yield "data: [DONE]\n\n"
            return
        chunk_size = 8 if "[steer-proof]" in prompt else 60
        for offset in range(0, len(text), chunk_size):
            yield await emit({"content": text[offset : offset + chunk_size]})
            if "[slow]" in prompt:
                await anyio.sleep(0.3)
        yield await emit({}, "stop")
        if include_usage:
            yield usage_chunk
        yield "data: [DONE]\n\n"

    async def observed_chunks():
        observation["stream_started_at"] = time.time()
        try:
            async for chunk in chunks():
                yield chunk
            observation["stream_completed_at"] = time.time()
        finally:
            observation["stream_closed_at"] = time.time()

    return StreamingResponse(observed_chunks(), media_type="text/event-stream")


def planned_tool(body: dict, prompt: str) -> dict | list[dict] | None:
    choices = (
        (
            "[client]",
            "local_review",
            {"prompt": "Review this fictional release. Supply a decision and a short reason."},
        ),
        ("[mcp]", "lookup_local_review", {"topic": "Fictional release navigation"}),
        ("[mcp-app]", "show_counter", {}),
        ("[mcp-fail]", "fail_local_review", {"topic": "Fictional release navigation"}),
        (
            "[delegate]",
            "delegate",
            {"subagent_name": "reviewer", "prompt": "Return a short fictional release review for the parent Agent."},
        ),
    )
    tools = [item["function"] for item in body.get("tools", [])]
    if finding_fixture.selection(prompt) is not None:
        plan = finding_fixture.steps(body.get("messages", []), prompt)
        made = sum(len(message.get("tool_calls") or []) for message in body.get("messages", []))
        if made >= len(plan):
            return None
        name, arguments = plan[made]
        selected = next((tool for tool in tools if tool["name"] == name), None)
        return call(selected["name"], arguments) if selected else None
    if "[workspace]" in prompt:
        return workspace_step(body, tools)
    if "[memory-edit]" in prompt:
        return memory_step(body, tools)
    if record := re.search(r'\[memory-record\] (\S+) "([^"]*)"', prompt):
        # `[memory-record] name "text"`: add one record to a mounted record memory.
        if any(tool["name"] == "memory_record_add" for tool in tools):
            return call("memory_record_add", {"memory": record[1], "text": record[2]})
        return None
    waiting = re.search(r"\[service-wait:(question|client|approval|mixed)\]", prompt)
    if waiting:
        kind = waiting[1]
        selected = []
        for tool in tools:
            name = tool["name"]
            if name == "ask_user_question" and kind in {"question", "mixed"}:
                arguments = {
                    "questions": [
                        {
                            "header": "Scope",
                            "question": "Which scope?",
                            "options": [
                                {"label": "Small", "description": "Use the selected scope"},
                                {"label": "All", "description": "Use the whole scope"},
                            ],
                            "multiSelect": False,
                        }
                    ]
                }
                identity = "call_question"
            elif name == "local_review" and kind in {"client", "mixed"}:
                arguments, identity = {"prompt": "Provide a manual JSON result"}, "call_client"
            elif name.startswith("increment_") and kind in {"approval", "mixed"}:
                arguments, identity = {"label": "waiting proof", "hold": "[hold-mcp]" in prompt}, "call_approval"
            else:
                continue
            item = call(name, arguments)
            item["id"] = identity
            selected.append(item)
        return selected or None
    if "[service-composio]" in prompt:
        selected = next((item for item in tools if item["name"].startswith("GITHUB_CREATE_ISSUE_")), None)
        if selected:
            return call(selected["name"], {"title": "Service managed action proof"})
    match = re.search(r"\[service-mcp:(increment_once|increment|read_count|oversized)\]", prompt)
    if match:
        selected = next((item for item in tools if item["name"].startswith(match[1] + "_")), None)
        if selected:
            result = call(selected["name"], {"label": "service proof", "hold": "[hold-mcp]" in prompt})
            if "[fixed-call-id]" in prompt:
                result["id"] = "call_same_original_id"
            return result
    if "[publish] " in prompt:
        selected = next((item for item in tools if item["name"].endswith("publish_asset")), None)
        path = prompt.split("[publish] ", 1)[1].splitlines()[0]
        if selected:
            return call(selected["name"], {"path": path, "media_type": "text/markdown"})
    if "[delegate]" in prompt:
        calls = [
            entry["function"]["name"] for message in body.get("messages", []) for entry in message.get("tool_calls", [])
        ]
        if any(name.endswith("wait_subagent") for name in calls):
            return None
        result = next(
            (
                str(message.get("content", ""))
                for message in reversed(body.get("messages", []))
                if message.get("role") == "tool"
            ),
            "",
        )
        if result:
            match = re.search(r'"execution_id"\s*:\s*"([^"]+)"', result)
            selected = next((item for item in tools if item["name"].endswith("wait_subagent")), None)
            if match and selected:
                return call(selected["name"], {"execution_id": match.group(1), "timeout_seconds": 10})
            return None
    for marker, name, arguments in choices:
        if marker not in prompt:
            continue
        selected = next(
            (item for item in tools if item["name"] == name or item["name"].endswith("_" + name)),
            None,
        )
        if selected:
            return call(selected["name"], arguments)
    if "[structured]" in prompt or "[structured-invalid]" in prompt:
        selected = next((item for item in tools if "outcome" in item.get("parameters", {}).get("properties", {})), None)
        if selected:
            output = call(
                selected["name"],
                {"outcome": "ready", "checks": ["keyboard", "navigation"], "summary": "Fictional structured review"},
            )
            if "[structured-invalid]" in prompt:
                output["function"]["arguments"] = "{malformed-json"
            return output
    return None


def workspace_step(body: dict, tools: list[dict]) -> dict | None:
    """`[workspace]`: read the first listed skill, write a note, then run a command, one call per model request."""
    messages = body.get("messages", [])
    marked = max(index for index, message in enumerate(messages) if "[workspace]" in text_of(message))
    made = sum(len(message.get("tool_calls") or []) for message in messages[marked:])
    instructions = "\n".join(text_of(message) for message in messages if message.get("role") == "system")
    skill = re.search(r"<path>([^<]+)</path>", instructions)
    note = "notes/review.md"
    steps = [
        *([("view", {"file_path": skill[1] + "/SKILL.md"})] if skill else []),
        ("write", {"file_path": f"/workspace/{note}", "content": "# Fictional review\n\n- Navigation\n- Keyboard\n"}),
        ("shell_exec", {"command": f"wc -l {note} && head -n 1 {note}", "cwd": "/workspace"}),
    ]
    if made >= len(steps):
        return None
    name, arguments = steps[made]
    return call(name, arguments) if any(tool["name"] == name for tool in tools) else None


def memory_step(body: dict, tools: list[dict]) -> dict | None:
    """`[memory-edit] name path "old" -> "new"`: view a mounted memory's file, then edit it, one call per request."""
    messages = body.get("messages", [])
    marked = max(index for index, message in enumerate(messages) if "[memory-edit]" in text_of(message))
    edit = re.search(r'\[memory-edit\] (\S+) (\S+) "([^"]*)" -> "([^"]*)"', text_of(messages[marked]))
    if edit is None:
        return None
    memory, path, old, new = edit.groups()
    steps = [
        ("memory_file_view", {"memory": memory, "path": path}),
        ("memory_file_edit", {"memory": memory, "path": path, "old_string": old, "new_string": new}),
    ]
    made = sum(len(message.get("tool_calls") or []) for message in messages[marked:])
    if made >= len(steps):
        return None
    name, arguments = steps[made]
    return call(name, arguments) if any(tool["name"] == name for tool in tools) else None


def text_of(message: dict) -> str:
    """A message's text: its content is a string, or a list of typed parts."""
    content = message.get("content") or ""
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))


def call(name: str, arguments: dict) -> dict:
    return {
        "id": "call_" + uuid4().hex[:16],
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


@contextmanager
def model_process(port: int = MODEL_PORT):
    """Own a listening socket before spawning; never reuse an unknown listener."""
    previous = {name: os.environ.get(name) for name in ("NO_PROXY", "no_proxy")}
    bypass = ",".join(filter(None, [*previous.values(), "127.0.0.1,localhost,::1"]))
    try:
        for name in previous:
            os.environ[name] = bypass
        with fixture_process("dev.fixtures.model", port=port) as url:
            yield url + "/v1"
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def serve_model(port: int = MODEL_PORT, mem0_records: str | None = None) -> None:
    if mem0_records:
        keep_mem0_records(Path(mem0_records))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int)
    args = parser.parse_args()
    uvicorn.run(app, fd=args.fd, host="127.0.0.1", port=MODEL_PORT, log_level="warning", access_log=False)
