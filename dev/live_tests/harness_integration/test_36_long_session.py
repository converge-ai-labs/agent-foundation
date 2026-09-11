"""Verify real continuation history and built-in compaction, without latency gates."""

import json
import logging
from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.anyio


async def test_long_session_compaction(long_session):
    lab, checkpoints = long_session
    live = lab.client
    case = await live.case("basic")
    source = previous = None
    trajectory = lab.root / "compaction.jsonl"
    for depth in range(1, checkpoints[-1] + 1):
        text = live.start_body(case)["input"]["content"][0]["text"] + f"\nLONG_SESSION_RUN {depth}"
        if depth == 1:
            text += f"\nLONG_SESSION_MEMORY {case['token']}"
        body = {"input": agent_input(text + "\n" + "x" * live.config["long_session"]["message_bytes"])}
        if source is None:
            path = f"/api/v1/workspaces/{live.config['workspace_id']}/runs"
            body["agent_id"] = live.config["agent_id"]
        else:
            path = f"/api/v1/runs/{source['id']}/continue"
            body["expected_thread_version"] = (await live.thread(source["thread_id"]))["version"]
        receipt = await live.request("POST", path, expected=202, headers={"Idempotency-Key": uuid4().hex}, json=body)
        live.track(receipt)
        settled = await live.finish(receipt["run_id"])
        assert settled["output_text"].startswith(f"{case['token']}\nLONG_SESSION_RUN {depth}\n")
        if source is not None:
            assert settled["parent_run_id"] == source["id"] and settled["thread_id"] == source["thread_id"]
        source = settled
        evidence = await live.request("GET", f"/__live__/session/runs/{source['id']}")
        assert evidence["sequence"] == depth and evidence["memories"] == [case["token"]]
        if previous is not None:
            assert evidence["compactions"] in {previous["compactions"], previous["compactions"] + 1}
            if evidence["compactions"] > previous["compactions"]:
                assert evidence["compacted"] and evidence["state_bytes"] < previous["state_bytes"]
        with trajectory.open("a") as output:
            output.write(json.dumps({"runs": depth, "run_id": source["id"], **evidence}) + "\n")
        previous = evidence
        if depth in checkpoints or depth % 50 == 0:
            logger.info(
                "session_compaction runs=%s/%s compactions=%s state_bytes=%s",
                depth,
                checkpoints[-1],
                evidence["compactions"],
                evidence["state_bytes"],
            )
    if checkpoints[-1] >= 1000:
        assert previous["compactions"] > 0, "Long chains must exercise built-in compaction"
