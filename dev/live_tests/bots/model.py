"""Read only actual model-visible references and reply with actual tool evidence."""

import json
import re

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..infrastructure.round_two_model import tool_call


def decoded(value):
    while isinstance(value, str):
        value = json.loads(value)
    return value


def event_cases(texts):
    """Read scenario markers from the actual canonical inbound event projection."""
    matches = []
    for text in texts:
        try:
            envelope = json.loads(text)
        except ValueError:
            continue
        if not isinstance(envelope, dict) or not isinstance(envelope.get("events"), list):
            continue
        for event in envelope["events"]:
            if isinstance(event, dict) and isinstance(event.get("text"), str):
                matches.extend(re.findall(r"btest_[a-f0-9]{32}\s+LIVE_TEST (\{[^\n]+\})$", event["text"]))
    return matches


def memory_index(body):
    from ..infrastructure.fixture_model import _message_text

    context = "\n".join(_message_text(message) for message in body["messages"])
    match = re.search(
        r'<memory-context source="MEMORY.md"[^>]*>.*?\n(\{[^\n]+\})\n</memory-context>', context, re.DOTALL
    )
    assert match, "No actual memory index reached the model"
    return json.loads(match[1])["text"]


async def completion(case, path, body):
    from ..infrastructure.fixture_model import _chunks, _message_text

    async with await anyio.Path(path / "observations.jsonl").open("a") as output:
        await output.write(json.dumps({"body": body}) + "\n")
    context = "\n".join(_message_text(message) for message in body["messages"])
    messages = [item for item in body["messages"] if item.get("role") == "tool"]
    tool = None
    answer = "done"
    if case.scenario == "bot_memory_retry" and not await anyio.Path(path / "release").exists():
        memory_index(body)
        raise HTTPException(401, "live_test_bot_model_unavailable")
    if case.scenario == "bot_memory_parent":
        plan = json.loads(await anyio.Path(path / "plan.json").read_text())
        if not messages:
            memory_index(body)
            arguments = {"subagent" if plan["mode"] == "inline" else "subagent_name": "child"}
            arguments["prompt"] = "LIVE_TEST " + json.dumps(plan["child"])
            tool = tool_call(body, "delegate", arguments)
            return StreamingResponse(_chunks(case, path, answer, tool), media_type="text/event-stream")
        messages = messages[1:]
    if not messages:
        index = memory_index(body)
        if case.scenario == "bot_memory_revoked":
            assert "memory://" not in index, "Revoked documents remain in the memory index"
            reference = re.search(r"STALE_MEMORY_REFERENCE memory://(mdoc_[a-z0-9]+)", context)
            assert reference, "No old document reference was supplied for the access check"
        else:
            reference = re.search(r"memory://(mdoc_[a-z0-9]+)", index)
            assert reference, "No authorized document reference reached the model"
        tool = tool_call(body, "memory_read", {"reference": "memory://" + reference[1], "length": 8000})
    elif len(messages) == 1:
        result = decoded(messages[-1]["content"])
        if case.scenario == "bot_memory_revoked":
            assert result.get("ok") is False and "text" not in result, result
            answer = "MEMORY_NOT_AVAILABLE"
        else:
            assert result.get("ok") is True, result
            answer = result["text"]
        if case.scenario == "bot_memory_child":
            return StreamingResponse(_chunks(case, path, answer, None), media_type="text/event-stream")
        marker = re.search(r"btest_[a-f0-9]{32}", context)
        assert marker, "No admitted Bot test marker reached the model"
        text = marker[0] + "\n" + answer
        if "slack.app_mention" in context:
            tool = tool_call(body, "slack.reply", {"text": text})
        else:
            assert "lark." in context
            tool = tool_call(body, "lark.reply", {"content": {"kind": "text", "text": text}})
    else:
        assert len(messages) == 2
        assert "succeeded" in messages[-1]["content"], "Native reply did not succeed"
    return StreamingResponse(_chunks(case, path, answer, tool), media_type="text/event-stream")
