"""A loopback-only upstream peer with per-case plans and independent wire evidence."""

import argparse
import hashlib
import json
from pathlib import Path

import anyio
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import wire


def peer_app(root: Path):
    app = FastAPI()

    @app.get("/readyz")
    async def ready():
        return {"status": "ready"}

    @app.api_route("/{case_id}/{path:path}", methods=["GET", "POST"])
    async def dispatch(case_id: str, path: str, request: Request):
        if len(case_id) != 32 or any(char not in "0123456789abcdef" for char in case_id):
            raise HTTPException(404)
        directory = anyio.Path(root / case_id)
        if not await (directory / "plan.json").is_file():
            raise HTTPException(404)
        plan = json.loads(await (directory / "plan.json").read_text())
        body = await request.json() if request.method == "POST" else None
        observation = {
            "method": request.method,
            "path": path,
            "query": dict(request.query_params),
            "body": body,
            "headers_sha256": {
                key: hashlib.sha256(value.encode()).hexdigest()
                for key, value in request.headers.items()
                if key in {"authorization", "x-api-key", "x-goog-api-key", "x-model-key", "x-team", "x-gateway-key"}
            },
        }
        async with await (directory / "requests.jsonl").open("a") as output:
            await output.write(json.dumps(observation) + "\n")
        if plan.get("status"):
            return JSONResponse(
                {"error": {"message": "Fixture upstream rejection", "type": "invalid_request_error"}},
                status_code=plan["status"],
            )
        if request.method == "GET":
            if plan.get("catalog_status"):
                return JSONResponse(
                    {"error": {"message": "Fixture catalog unavailable"}}, status_code=plan["catalog_status"]
                )
            if plan.get("empty_catalog"):
                return {"object": "list", "data": [], "has_more": False}
            second = "after" in request.query_params
            names = ["fixture-z", "fixture-a"] if not second else ["fixture-a", "manual-model"]
            return {
                "object": "list",
                "data": [{"id": name, "object": "model", "created": 1, "owned_by": "fixture"} for name in names],
                "has_more": not second,
                "last_id": "page-two" if not second else "manual-model",
            }
        requests = [json.loads(line) for line in (await (directory / "requests.jsonl").read_text()).splitlines()]
        count = sum(item["method"] == "POST" for item in requests)
        if plan.get("gate_at") == count:
            await (directory / "ready").touch()
            with anyio.fail_after(100):
                while not await (directory / "release").exists():
                    await anyio.sleep(0.05)
        answer, tool = plan["answer"], None
        replies = [message for message in body.get("messages", []) if message.get("role") == "tool"]
        replies += [
            item
            for item in body.get("input", [])
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        if plan.get("tool") and not replies:
            definitions = [item.get("function", item) for item in body.get("tools", [])]
            name = next(
                item["name"]
                for item in definitions
                if (
                    "answer" in item.get("parameters", {}).get("properties", {})
                    if plan["tool"] == "$output"
                    else item["name"].endswith(plan["tool"])
                )
            )
            tool = {
                "id": "call_fixture",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(plan.get("arguments", {}))},
            }
        elif replies:
            answer = json.dumps(replies[-1].get("content", replies[-1].get("output")))
        if path.endswith("chat/completions"):
            result = wire.chat(body, answer, tool)
        elif path.endswith("responses"):
            result = wire.responses(body, answer, tool)
        elif path.endswith("messages"):
            result = wire.anthropic(body, answer)
        elif "GenerateContent" in path or "generateContent" in path:
            result = wire.google(answer, "streamGenerateContent" in path)
        else:
            raise HTTPException(404)
        return Response(result, media_type="text/event-stream") if isinstance(result, str) else result

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(peer_app(args.root), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
