"""Local scripted OpenAI-compatible model for UI development, not real inference."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import anyio
import httpx2
import uvicorn
from a13n_service.connectivity.toolsets import portable_tool_name
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .fixture_api import router as fixture_router

MODEL_PORT = 18080
MODEL_URL = f"http://127.0.0.1:{MODEL_PORT}/v1"
app = FastAPI()
app.include_router(fixture_router)


@app.get("/healthz")
async def health():
    return {"status": "ok", "model": "local-scripted"}


@app.post("/v1/chat/completions")
async def completion(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    prompt = "\n".join(str(message.get("content", "")) for message in messages if message.get("role") == "user")
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
        if tool_result is None or any(flag in prompt for flag in ("[structured-invalid]", "[delegate]"))
        else None
    )
    document = Path(__file__).with_name("fixtures").joinpath("response.md").read_text()
    text = (
        document
        if "[long]" in prompt
        else (
            "This is a local scripted response for UI development.\n\n"
            "已收到你的消息。这里使用虚构内容，方便检查排版、交互与运行记录。\n\n" + document.split("\n\n", 1)[-1][:220]  # noqa: RUF001
        )
    )
    if tool_result is not None:
        text = (
            "## Local tool result\n\nThe local fixture returned:\n\n```json\n" + str(tool_result) + "\n```\n\n" + text
        )
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
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "local-scripted",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [tool_call]}
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
            yield await emit({"tool_calls": [{"index": 0, **tool_call}]})
            yield await emit({}, "tool_calls")
            if include_usage:
                yield usage_chunk
            yield "data: [DONE]\n\n"
            return
        for offset in range(0, len(text), 60):
            yield await emit({"content": text[offset : offset + 60]})
            if "[slow]" in prompt:
                await anyio.sleep(0.3)
        yield await emit({}, "stop")
        if include_usage:
            yield usage_chunk
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")


def planned_tool(body: dict, prompt: str) -> dict | None:
    choices = (
        (
            "[client]",
            "local_review",
            {"prompt": "Review this fictional release. Supply a decision and a short reason."},
        ),
        ("[mcp]", "lookup_local_review", {"topic": "Fictional release navigation"}),
        ("[mcp-fail]", "fail_local_review", {"topic": "Fictional release navigation"}),
        (
            "[delegate]",
            "delegate",
            {"subagent_name": "reviewer", "prompt": "Return a short fictional release review for the parent Agent."},
        ),
    )
    tools = [item["function"] for item in body.get("tools", [])]
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
            (
                item
                for item in tools
                if item["name"] == name
                or item["name"].endswith("_" + name)
                or item["name"].endswith("_" + portable_tool_name(name))
            ),
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
        with _model_process(port) as url:
            yield url
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def _model_process(port: int):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "dev.service.model", "--fd", str(listener.fileno())],
            pass_fds=(listener.fileno(),),
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.DEVNULL,
        )
        try:
            with httpx2.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Local model process exited during startup")
                    try:
                        response = client.get(f"http://127.0.0.1:{port}/healthz")
                        response.raise_for_status()
                        break
                    except httpx2.HTTPError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Local model did not become ready") from None
                        time.sleep(0.1)
            yield f"http://127.0.0.1:{port}/v1"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def serve_model(port: int = MODEL_PORT) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fd", type=int)
    args = parser.parse_args()
    uvicorn.run(app, fd=args.fd, host="127.0.0.1", port=MODEL_PORT, log_level="warning", access_log=False)
