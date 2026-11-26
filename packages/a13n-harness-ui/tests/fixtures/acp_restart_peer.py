"""ACP stdio peer that records protocol order across process replacements."""

import json
import sys
from pathlib import Path


def main() -> None:
    journal, transport, behavior = sys.argv[1:]
    path = Path(journal)
    previous = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    prompts = sum(item["method"] == "session/prompt" for item in previous)
    pending = None
    session_id = "restart-fixture"
    mode = {
        "id": "mode",
        "type": "select",
        "name": "Mode",
        "category": "mode",
        "currentValue": "plan",
        "options": [{"value": "chat", "name": "Chat"}, {"value": "plan", "name": "Plan"}],
    }

    def send(**message):
        print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)

    def reply_text(text):
        send(
            method="session/update",
            params={
                "sessionId": session_id,
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}},
            },
        )

    for line in sys.stdin:
        message = json.loads(line)
        method, params, request_id = message["method"], message.get("params", {}), message.get("id")
        with path.open("a") as log:
            log.write(json.dumps({"method": method, "params": params}) + "\n")
        if method == "initialize":
            capabilities = {"loadSession": transport == "load"}
            if transport == "resume":
                capabilities["sessionCapabilities"] = {"resume": {}}
            send(id=request_id, result={"protocolVersion": 1, "agentCapabilities": capabilities})
        elif method == "session/new":
            send(id=request_id, result={"sessionId": session_id, "configOptions": [mode]})
        elif method in {"session/resume", "session/load"}:
            assert params["sessionId"] == session_id
            if behavior == "resume-error":
                send(id=request_id, error={"code": -32603, "message": "Test peer cannot reopen session"})
            else:
                reopened = {**mode, "currentValue": "chat"}
                if behavior == "options-error":
                    reopened["options"] = [mode["options"][0]]
                send(id=request_id, result={"configOptions": [reopened]})
        elif method == "session/set_config_option":
            assert params["configId"] == "mode" and params["value"] == "plan"
            send(id=request_id, result={"configOptions": [mode]})
        elif method == "session/prompt":
            prompts += 1
            if (behavior == "chained" and prompts <= 2) or (
                prompts == 1 and behavior not in {"goal-boundary", "early"}
            ):
                pending = request_id
                if behavior == "goal-compaction":
                    send(
                        method="session/update",
                        params={
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "compaction_update",
                                "compactionId": "test",
                                "status": "completed",
                            },
                        },
                    )
                reply_text(f"Partial reply {prompts}.")
                send(
                    method="session/update",
                    params={
                        "sessionId": session_id,
                        "update": {"sessionUpdate": "usage_update", "used": 34, "size": 100},
                    },
                )
            else:
                reply_text(
                    "ACP finished.\n[GOAL_COMPLETE]"
                    if behavior.startswith("goal-") and prompts > 1
                    else "ACP finished."
                )
                send(id=request_id, result={"stopReason": "end_turn"})
        elif method == "session/cancel" and pending is not None:
            reason = "cancelled"
            if behavior == "natural":
                reply_text(" Finished before shutdown.")
                reason = "end_turn"
            send(
                id=pending,
                result={"stopReason": reason, "usage": {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18}},
            )
            pending = None
        elif request_id is not None:
            send(id=request_id, error={"code": -32601, "message": "Unimplemented fixture method"})


if __name__ == "__main__":
    main()
