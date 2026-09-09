"""Script model decisions, while observing the actual Harness request and tool results.

Plans stay outside Agent input. In particular, expected file bytes and credentials
must never be echoed from a plan as evidence of successful execution.
"""

import hashlib
import html
import json
import posixpath
import re
from uuid import uuid4

import anyio
from fastapi.responses import StreamingResponse

from .round_two_model import gate, tool_call

SCENARIOS = {"management"}


async def completion(case, path, body, request):
    from .fixture_model import _chunks

    plan = json.loads(await anyio.Path(path / "plan.json").read_text())
    observation = {
        "body": body,
        "endpoint": request.url.path,
        "authorization_sha256": hashlib.sha256(request.headers.get("authorization", "").encode()).hexdigest(),
    }
    async with await anyio.Path(path / "observations.jsonl").open("a") as output:
        await output.write(json.dumps(observation) + "\n")
    # Continuations may contain earlier journeys; only this case's tool results advance its plan.
    start = max(
        index
        for index, message in enumerate(body["messages"])
        if message.get("role") == "user" and case.case_id in json.dumps(message.get("content"))
    )
    messages = [message for message in body["messages"][start + 1 :] if message.get("role") == "tool"]
    steps = plan.get("steps", [])
    index = len(messages)
    if plan.get("gate_at") == index:
        await gate(path, "management_ready")
    tool = None
    if plan.get("parallel_steps") and not messages:
        tool = [tool_call(body, step["tool"], step["arguments"]) for step in plan["parallel_steps"]]
        for index, call in enumerate(tool):
            call["index"] = index
    if index < len(steps):
        step = steps[index]
        name = step["tool"]
        arguments = dict(step.get("arguments", {}))
        from .fixture_model import _message_text

        context = "\n".join(_message_text(message) for message in body["messages"])
        if "skill_file" in step:
            match = re.search(
                r'<skill name="' + re.escape(step["skill_key"]) + r'">.*?<path>(.*?)</path>', context, re.S
            )
            assert match, "The managed Skill is missing from the actual Harness catalog"
            arguments["file_path"] = posixpath.join(html.unescape(match[1]), step["skill_file"])
        if step.get("materialized_input"):
            paths = re.findall(r'/workspace/\.a13n/inputs/[^\s"<>]+/content-[0-9]+', context)
            assert paths, "Harness did not receive the materialized Asset path"
            arguments["file_path"] = paths[-1]
        if "publication_path" in step:
            definition = next(
                entry["function"]
                for entry in body.get("tools", [])
                if entry["function"]["name"].endswith("publish_asset")
            )
            fields = definition["parameters"]["properties"]
            path_field = next(key for key in ("path", "file_path") if key in fields)
            arguments[path_field] = step["publication_path"]
        if name == "$output":
            candidates = [entry["function"] for entry in body.get("tools", [])]
            name = next(entry["name"] for entry in candidates if "answer" in entry["parameters"].get("properties", {}))
        if step.get("force_unadvertised"):
            tool = {
                "index": 0,
                "id": "call_" + uuid4().hex,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments),
                },
            }
        else:
            tool = tool_call(body, name, arguments)
        if "raw_arguments" in step:
            tool["function"]["arguments"] = step["raw_arguments"]
    # A completed tool journey returns its actual tool result, never the expected value.
    answer = json.dumps(messages[-1]["content"]) if messages else case.token
    return StreamingResponse(_chunks(case, path, answer, tool), media_type="text/event-stream")
