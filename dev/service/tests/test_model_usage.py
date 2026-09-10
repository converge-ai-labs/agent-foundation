"""The scripted model supplies requested streaming usage for trace validation."""

import json

import httpx2
import pytest

from dev.service.model import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("include_usage", [False, True])
@pytest.mark.parametrize("tool_call", [False, True])
async def test_stream_reports_usage_only_when_requested(include_usage, tool_call):
    prompt = "[delegate] Review a fictional release" if tool_call else "Fictional greeting"
    body = {
        "model": "local-scripted",
        "stream": True,
        "stream_options": {"include_usage": include_usage},
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "function", "function": {"name": "delegate", "parameters": {"type": "object"}}}],
    }
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    rows = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    usage_rows = [row for row in rows if "usage" in row]
    assert len(usage_rows) == int(include_usage)
    assert response.text.endswith("data: [DONE]\n\n")
    if include_usage:
        usage = usage_rows[0]["usage"]
        assert usage_rows[0]["choices"] == []
        assert usage["prompt_tokens"] == 20
        assert usage["completion_tokens"] > 0
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
