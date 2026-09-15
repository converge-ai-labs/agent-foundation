"""Native Responses stream failures served by the owned TLS peer."""

import json

import anyio
from fastapi.responses import StreamingResponse

from ..infrastructure.run_faults import Faults
from .wire import responses, sse


async def completion(case, path, body):
    plan = json.loads(await anyio.Path(path / "plan.json").read_text())
    observations = anyio.Path(path / "observations.jsonl")
    async with await observations.open("a") as output:
        await output.write(json.dumps({"body": body}) + "\n")
    number = len((await observations.read_text()).splitlines())
    failing = number <= plan.get("failures", 0)
    answer = "PARTIAL_MUST_NOT_COMPLETE" if failing else case.token

    async def chunks():
        for line in responses(body, answer).splitlines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            if event["type"] == "response.created":
                # Final usage is only reported by response.completed, never by
                # the initial in-progress response or partial text deltas.
                event["response"]["usage"] = None
            if failing and event["type"] == "response.output_item.done":
                if plan["failure"] == "paused":
                    await Faults(path.parent.parent / "faults", "control").reach(
                        "responses.stream_paused", case_id=case.case_id
                    )
                raise RuntimeError("Injected Responses stream disconnect before completion")
            yield sse([event], named=True)

    return StreamingResponse(chunks(), media_type="text/event-stream")
