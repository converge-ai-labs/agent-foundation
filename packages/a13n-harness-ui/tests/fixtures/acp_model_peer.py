"""Stdio ACP peer for first-prompt model context, echoing received text blocks."""

import json
import sys
from time import sleep


def send(**message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def update(session_update, **fields):
    send(
        method="session/update",
        params={"sessionId": "model-session", "update": {"sessionUpdate": session_update, **fields}},
    )


mode = sys.argv[1]
model_option = {
    "id": "provider-model" if mode == "delayed" else "model",
    "name": "Model",
    "type": "select",
    "currentValue": "" if mode == "unselected" else "fixture-model",
    "options": [{"value": "fixture-model", "name": "Fixture Model"}],
}
if mode == "delayed":
    model_option["category"] = "model"
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if "id" not in request:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"sessionCapabilities": {"systemPrompt": {}}}}
    elif method == "session/new":
        result = {"sessionId": "model-session"}
        if mode == "legacy":
            result["models"] = {
                "currentModelId": "fixture-model",
                "availableModels": [{"modelId": "fixture-model", "name": "Fixture Model"}],
            }
        elif mode in {"standard", "unselected"}:
            result["configOptions"] = [model_option]
    elif method == "session/prompt":
        text = "\n".join(block["text"] for block in request["params"]["prompt"] if block["type"] == "text")
        update("agent_message_chunk", content={"type": "text", "text": text})
        result = {"stopReason": "end_turn"}
    else:
        raise AssertionError(method)
    send(id=request["id"], result=result)
    if method == "session/new" and mode == "delayed":
        sleep(0.05)
        update("config_option_update", configOptions=[model_option])
