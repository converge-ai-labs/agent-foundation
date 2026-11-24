"""Small real stdio peer. Assertions in the peer exercise the raw wire contract."""

import json
import os
import subprocess
import sys
import traceback
from itertools import count
from urllib.request import Request, urlopen


def log_failure(*args):
    with open("peer-error.log", "w") as file:
        traceback.print_exception(*args, file=file)


sys.excepthook = log_failure

IDS = count(100)
SESSION = "fixture-session"
MODE = sys.argv[1]
STEERING = MODE != "load"
PENDING = None
MCP = None
TERMINAL = None


def send(**data):
    print(json.dumps({"jsonrpc": "2.0", **data}), flush=True)


def update(kind, **fields):
    send(method="session/update", params={"sessionId": SESSION, "update": {"sessionUpdate": kind, **fields}})


def text(value):
    update("agent_message_chunk", content={"type": "text", "text": value})


def callback(method, /, **params):
    request_id = next(IDS)
    send(id=request_id, method=method, params=params)
    answer = json.loads(sys.stdin.readline())
    assert answer["id"] == request_id, answer
    return answer


def result(method, /, **params):
    answer = callback(method, **params)
    assert "result" in answer, answer
    return answer["result"]


def session_call(method, **params):
    return result(method, sessionId=SESSION, **params)


def mcp(method, params=None, **meta):
    params = params or {}
    if MCP["type"] == "acp":
        return result("mcp/message", connectionId=MCP["connectionId"], method=method, params=params, _meta=meta)
    line = json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params}).encode() + b"\n"
    if MCP["type"] == "http":
        headers = {header["name"]: header["value"] for header in MCP["headers"]}
        headers["Content-Type"] = "application/json"
        with urlopen(Request(MCP["url"], line, headers), timeout=5) as response:
            answer = json.load(response)
    else:
        TERMINAL.stdin.write(line)
        TERMINAL.stdin.flush()
        answer = json.loads(TERMINAL.stdout.readline())
    assert "result" in answer, answer
    return answer["result"]


for line in sys.stdin:
    frame = json.loads(line)
    method = frame.get("method")
    params = frame.get("params", {})
    ident = frame.get("id")
    if method == "initialize":
        caps = {"mcpCapabilities": {"acp": MODE == "remote", "http": MODE == "http"}, "loadSession": True}
        if STEERING:
            caps["sessionCapabilities"] = {"resume": {}, "systemPrompt": {}, "additionalDirectories": {}}
        send(
            id=ident,
            result={
                "protocolVersion": 1,
                "agentCapabilities": caps,
                "_meta": {"dev.a13n.harness-ui": {"version": 1}} if STEERING else {},
            },
        )
    elif method in {"session/new", "session/load", "session/resume"}:
        assert (method == "session/load") == (MODE == "load")
        if STEERING:
            assert params["systemPrompt"] == {"mode": "append", "content": [{"type": "text", "text": "guide"}]}
        if method == "session/load":
            text("OLD TRANSCRIPT")
        if params.get("mcpServers"):
            (MCP,) = params["mcpServers"]
            MCP.setdefault("type", "stdio")
            if MCP["type"] == "acp":
                MCP.update(result("mcp/connect", serverId=MCP["serverId"]))
            elif MCP["type"] == "stdio":
                TERMINAL = subprocess.Popen(
                    [MCP["command"], *MCP["args"]],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    env={**os.environ, **{v["name"]: v["value"] for v in MCP["env"]}},
                )
            assert mcp("initialize", {"protocolVersion": "2025-11-25"})["capabilities"] == {"tools": {}}
        send(id=ident, result={"sessionId": SESSION} if method == "session/new" else {})
    elif method == "session/prompt":
        blocks = [item["text"] for item in params["prompt"]]
        if not STEERING:
            assert blocks in (["guide", "wait"], ["again"]), blocks
        message = blocks[-1]
        if message == "wait":
            PENDING = ident
            send(
                id=90,
                method="session/request_permission",
                params={
                    "sessionId": SESSION,
                    "toolCall": {"toolCallId": "permission", "kind": "execute"},
                    "options": [{"name": "Allow", "optionId": "allow", "kind": "allow_once"}],
                },
            )
            continue
        if message == "callbacks":
            path = "/workspace/sample.txt"
            session_call("fs/write_text_file", path=path, content="one\ntwo\nthree\n")
            assert session_call("fs/read_text_file", path=path, line=2, limit=1)["content"] == "two\n"
            assert session_call("fs/read_text_file", path=path)["content"] == "one\ntwo\nthree\n"
            terminal = session_call(
                "terminal/create",
                command=sys.executable,
                args=["-c", "print('012345é界')"],
                cwd="/workspace",
                outputByteLimit=5,
            )["terminalId"]
            assert session_call("terminal/wait_for_exit", terminalId=terminal)["exitCode"] == 0
            output = session_call("terminal/output", terminalId=terminal)
            assert output["output"] == "界\n" and output["truncated"], output
            session_call("terminal/release", terminalId=terminal)
            denied = callback("terminal/create", sessionId=SESSION, command="denied")
            assert denied["error"]["data"]["code"] == "acp_command_denied"
        if MCP:
            assert [entry["name"] for entry in mcp("tools/list")["tools"]] == ["identify"]
            for key in ("one", "two"):
                update("tool_call", toolCallId=key, title="Identify", name="identify", status="in_progress")
                tool = mcp(
                    "tools/call",
                    {"name": "identify", "arguments": {}},
                    **({"toolCallId": "explicit"} if key == "two" and MODE == "remote" else {}),
                )
                text(tool["content"][0]["text"])
            tool = mcp("tools/call", {"name": "identify", "arguments": {}})
            text(tool["content"][0]["text"])
            for key in ("one", "two"):
                update("tool_call_update", toolCallId=key, status="completed")
        update("user_message_chunk", content={"type": "text", "text": "HIDDEN ECHO"})
        text("done:" + message)
        send(id=ident, result={"stopReason": "end_turn"})
    elif method == "_a13n/steer":
        assert PENDING is not None
        send(id=ident, result={"delivery": "accepted"})
    elif method == "session/cancel":
        if PENDING is not None:
            send(id=PENDING, result={"stopReason": "cancelled"})
            PENDING = None
    elif method is None and ident == 90:
        if PENDING is not None and frame["result"]["outcome"]["outcome"] == "selected":
            text("allowed")
            send(id=PENDING, result={"stopReason": "end_turn"})
            PENDING = None
    else:
        raise AssertionError(frame)

if TERMINAL is not None:
    TERMINAL.stdin.close()
    TERMINAL.wait(5)
