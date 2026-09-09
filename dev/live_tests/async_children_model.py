"""Independent parent/child model barriers for the async-result status matrix."""

import json
import re

import anyio
from fastapi import HTTPException

from .round_two_model import tool_call


async def gate(path, name, release):
    await anyio.Path(path / name).touch()
    with anyio.fail_after(180):
        while not any((path / value).exists() for value in (release, "release")):
            await anyio.sleep(0.05)


async def response(case, path, body, texts, tool_messages):
    child = next((match for text in texts if (match := re.search(r"^LIVE_CHILD ([01])$", text, re.MULTILINE))), None)
    if child:
        index = child[1]
        await gate(path, f"child_{index}_started", "children_release")
        return None, f"CHILD_RESULT_{index}_{case.token}"
    deliveries = [
        json.loads(match)["child_run_id"]
        for text in texts
        for match in re.findall(r"Trusted Host provenance: (\{[^\n]+\})", text)
    ]
    async with await anyio.Path(path / "result_deliveries").open("a") as output:
        await output.write(json.dumps(deliveries) + "\n")
    delegated = [message for message in tool_messages if "execution_id" in str(message.get("content"))]
    if len(delegated) < 2:
        return tool_call(
            body,
            "delegate",
            {
                "subagent_name": "child",
                "prompt": "LIVE_TEST " + case.model_dump_json() + f"\nLIVE_CHILD {len(delegated)}",
            },
        ), ""
    await gate(path, "parent_ready", "parent_release")
    mode = (path / "parent_mode").read_text()
    if mode == "failed":
        raise HTTPException(401, "live_test_parent_failure_after_delegation")
    if mode in {"approval", "client_tool"} and len(tool_messages) == 2:
        name = "live_approved_write" if mode == "approval" else "live_client"
        arguments = (
            {"case_id": case.case_id, "token": case.token} if mode == "approval" else {"prompt": "Resolve the wait"}
        )
        return tool_call(body, name, arguments), ""
    observed = [index for index in (0, 1) if f"CHILD_RESULT_{index}_{case.token}" in "\n".join(texts)]
    return None, "children-seen:" + ",".join(map(str, observed))
