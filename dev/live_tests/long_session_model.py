"""Size-aware deterministic model for sequential Run and compaction measurements."""

import json
import math
import re
import time
from uuid import uuid4

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .round_two_model import count, gate


def history_evidence(text):
    memories = set(re.findall(r"LONG_SESSION_MEMORY ([a-f0-9]{32})", text))
    return {
        "memories": sorted(memories),
        "sequence": max(map(int, re.findall(r"LONG_SESSION_RUN (\d+)", text)), default=0),
        "compactions": max(map(int, re.findall(r"LONG_SESSION_COMPACTIONS (\d+)", text)), default=0),
    }


def message_text(message):
    content = message.get("content", "")
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content)
    return content or ""


def response_content(body, token, message_bytes):
    # A documented, deterministic estimate, not a provider tokenizer. Include
    # message framing and tool definitions so usage grows with the actual request.
    prompt_tokens = math.ceil(
        len(json.dumps({key: body[key] for key in ("messages", "tools") if key in body}).encode()) / 4
    )
    text = "\n".join(message_text(message) for message in body["messages"])
    evidence = history_evidence(text)
    if len(evidence["memories"]) != 1:
        raise ValueError("The initial Run's memory must survive every continuation")
    # Harness appends its runtime context after the summary directive; the
    # directive is not necessarily the last provider message.
    compact = any(
        message["role"] == "user"
        and "Generate a compact continuation summary for the conversation history." in message_text(message)
        for message in body["messages"]
    )
    memory = evidence["memories"][0]
    if compact:
        answer = (
            "## Condensed conversation summary\n\n"
            f"LONG_SESSION_MEMORY {memory}\n"
            f"LONG_SESSION_RUN {evidence['sequence']}\n"
            f"LONG_SESSION_COMPACTIONS {evidence['compactions'] + 1}\n"
            "Continue the sequential Run benchmark and preserve the initial memory."
        )
    else:
        answer = f"{token}\nLONG_SESSION_RUN {evidence['sequence']}\n" + "y" * message_bytes
    completion_tokens = math.ceil(len(answer.encode()) / 4)
    return (
        answer,
        compact,
        {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    )


async def completion(case, path, body, config):
    answer, compact, usage = response_content(body, case.token, config["message_bytes"])
    if usage["total_tokens"] > config["context_window"]:
        raise HTTPException(400, "live_context_window_exceeded")
    if not compact:
        if case.scenario == "model_error" and not await anyio.Path(path / "release").exists():
            await count(path, "injected_errors")
            raise HTTPException(401, "live_test_invalid_model_credential")
        if case.scenario == "gate":
            await gate(path, "gate_ready")
    await count(path, "compaction_requests" if compact else "ordinary_requests")

    async def chunks():
        base = {
            "id": "chatcmpl_" + uuid4().hex,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "live-fixture",
        }
        for delta, finish in (({"role": "assistant"}, None), ({"content": answer}, None), ({}, "stop")):
            yield (
                "data: "
                + json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
                + "\n\n"
            )
        yield "data: " + json.dumps({**base, "choices": [], "usage": usage}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")
