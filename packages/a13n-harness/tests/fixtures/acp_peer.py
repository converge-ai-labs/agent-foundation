"""Small real stdio peer. Assertions in the peer exercise the raw wire contract."""

import json
import os
import re
import subprocess
import sys
import traceback
from itertools import count
from pathlib import Path
from time import sleep
from urllib.request import Request, urlopen


def log_failure(*args):
    with open("peer-error.log", "w") as file:
        traceback.print_exception(*args, file=file)


sys.excepthook = log_failure

IDS = count(100)
SESSION = "fixture-session"
MODE = sys.argv[1]
FLAGS = set(sys.argv[2:])
RESIDENT_ONLY = MODE == "ui" and "resident" in sys.argv[2:]
CATALOG = ["identify", "ask_user_question"] if "questions" in sys.argv[2:] else ["identify"]
STEERING = not RESIDENT_ONLY and MODE not in {"load", "new-no-system", "legacy"}
PENDING = None
ADDITIONAL = ()
CONFIG_OPTIONS = [
    dict(
        id="mode",
        name="Session mode",
        category="mode",
        type="select",
        currentValue="chat",
        options=[dict(value="chat", name="Chat"), dict(value="plan", name="Plan")],
    )
]
MODEL = {
    "id": "model",
    "name": "Model",
    "category": "model",
    "type": "select",
    "options": [{"value": "a", "name": "Model A"}, {"value": "b", "name": "Model B"}],
}
if "null-model" in FLAGS:
    MODEL["currentValue"] = None
MODEL_GATE = next((Path(arg.removeprefix("model-gate=")) for arg in FLAGS if arg.startswith("model-gate=")), None)
MODEL_ON_OPEN = bool({"null-model", "missing-model"} & FLAGS or MODEL_GATE) and "delayed-model" not in FLAGS
MCP = None
TERMINAL = None
SYSTEM_PROMPT = None
STEER_PROMPTS = []


def send(**data):
    print(json.dumps({"jsonrpc": "2.0", **data}), flush=True)


def update(update_type, **fields):
    send(method="session/update", params={"sessionId": SESSION, "update": {"sessionUpdate": update_type, **fields}})


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


def extract_file_path(message):
    for line in message.splitlines():
        if line.startswith("File: "):
            return line.removeprefix("File: ").strip()
        match = re.match(r"^Attachment .+ \([^)]+\): (.+?)\.(?: Selected Host source: .+)?$", line)
        if match:
            return match.group(1)
    return None


def file_content(message):
    path = extract_file_path(message)
    assert path is not None, message
    assert not any(os.path.commonpath((directory, path)) == directory for directory in ADDITIONAL), ADDITIONAL
    content = session_call("fs/read_text_file", path=path)["content"]
    with open(path, encoding="utf-8") as file:
        assert file.read() == content
    return content


def mcp(method, params=None, *, notification=False, expect_error=False, **meta):
    params = params or {}
    frame = {"method": method, "params": params}
    if MCP["type"] == "acp":
        fields = dict(connectionId=MCP["connectionId"], **frame, _meta=meta)
        if notification:
            send(method="mcp/message", params=fields)
            return None
        answer = callback("mcp/message", **fields)
        return answer["error"] if expect_error else answer["result"]
    if not notification:
        frame["id"] = 7
    line = json.dumps({"jsonrpc": "2.0", **frame}).encode() + b"\n"
    if MCP["type"] == "http":
        headers = {header["name"]: header["value"] for header in MCP["headers"]}
        headers["Content-Type"] = "application/json"
        with urlopen(Request(MCP["url"], line, headers), timeout=5) as response:
            if notification:
                assert response.status == 202
                return None
            answer = json.load(response)
    else:
        TERMINAL.stdin.write(line)
        TERMINAL.stdin.flush()
        if notification:
            return None
        answer = json.loads(TERMINAL.stdout.readline())
    assert ("error" if expect_error else "result") in answer, answer
    return answer["error"] if expect_error else answer["result"]


for line in sys.stdin:
    frame = json.loads(line)
    method = frame.get("method")
    params = frame.get("params", {})
    ident = frame.get("id")
    if method == "initialize":
        client_caps = params.get("clientCapabilities") or {}
        client_session = client_caps.get("session") if isinstance(client_caps, dict) else None
        if "require-compaction-cap" in FLAGS:
            assert isinstance(client_session, dict) and client_session.get("compaction") is not None, client_caps
        prompt_caps = {}
        if "prompt-image" in FLAGS:
            prompt_caps["image"] = True
        if "prompt-audio" in FLAGS:
            prompt_caps["audio"] = True
        if "embedded-context" in FLAGS:
            prompt_caps["embeddedContext"] = True
        caps = {
            "mcpCapabilities": {"acp": MODE == "remote", "http": MODE == "http" or "use-http" in FLAGS},
            "loadSession": not RESIDENT_ONLY,
            **({"promptCapabilities": prompt_caps} if prompt_caps else {}),
        }
        if MODE in {"auth", "auth-required"}:
            caps["auth"] = {"logout": {}}
        if MODE == "legacy":
            caps["_meta"] = {"antigravity": {"systemPrompt": {"append": True}}}
        if STEERING:
            caps["sessionCapabilities"] = {"resume": {}, "systemPrompt": {}, "additionalDirectories": {}}
        send(
            id=ident,
            result={
                "protocolVersion": 1,
                "agentCapabilities": caps,
                "authMethods": [
                    {"id": "browser", "name": "Browser login"},
                    {
                        "id": "terminal",
                        "name": "Terminal login",
                        "type": "terminal",
                        "args": ["--login"],
                        "env": {"FIXTURE_LOGIN": "fixture-secret"},
                    },
                    {"id": "unknown", "name": "Unknown flow", "type": "env"},
                ]
                if MODE in {"auth", "auth-required"}
                else [],
                "_meta": {"steering": {"supported": True}}
                if MODE == "standard-steer" or "standard-steer" in FLAGS
                else {"dev.a13n.harness-ui": {"version": 1}}
                if STEERING
                else {},
            },
        )
    elif MODE == "auth" and method == "authenticate":
        assert params["methodId"] == "browser"
        answer = result(
            "elicitation/create",
            message="Complete sign-in",
            mode="url",
            url="https://example.com/sign-in",
            elicitationId="auth-example",
            requestId=ident,
        )
        if answer["action"] == "accept":
            send(id=ident, result={})
        else:
            send(id=ident, error={"code": -32800, "message": "Cancelled"})
    elif MODE == "auth" and method == "logout":
        send(id=ident, result={})
    elif MODE == "auth-required" and method == "session/new":
        send(id=ident, error={"code": -32000, "message": "Authentication required"})
    elif method in {"session/load", "session/resume"} and MODE == "ui" and "never-persisted" in sys.argv[2:]:
        send(id=ident, error={"code": -32602, "message": "Unknown session"})
    elif method in {"session/new", "session/load", "session/resume"}:
        ADDITIONAL = params.get("additionalDirectories", ())
        SYSTEM_PROMPT = params.get("systemPrompt")
        assert (method == "session/load") == (MODE == "load")
        if STEERING and MODE not in {"ui", "auth"}:
            assert params["systemPrompt"]["mode"] == "append"
            assert params["systemPrompt"]["content"][0] == {"type": "text", "text": "guide"}
        if MODE == "legacy":
            assert params["_meta"]["antigravity"]["systemPrompt"]["append"] == "guide"
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
            mcp("notifications/initialized", notification=True)
            catalog = [entry["name"] for entry in mcp("tools/list")["tools"]]
            if MODE != "ui":
                assert set(catalog) == set(CATALOG)
            assert mcp("tools/call", {"name": "identify"}, expect_error=True)["data"]["code"] == "acp_run_inactive"
        if method == "session/new" and "unique-session" in sys.argv[2:]:
            SESSION = f"fixture-session-{os.getpid()}-{next(IDS)}"
        response = {"sessionId": SESSION} if method == "session/new" else {}
        if MODE == "ui" or (MODE not in {"legacy", "update"} and MODEL_ON_OPEN):
            if MODEL_ON_OPEN and (MODEL_GATE is None or MODEL_GATE.exists()) and MODEL not in CONFIG_OPTIONS:
                CONFIG_OPTIONS.append(MODEL)
            response["configOptions"] = CONFIG_OPTIONS
        if MODE == "legacy":
            response["models"] = {
                "currentModelId": "a",
                "availableModels": [{"modelId": "a", "name": "A"}, {"modelId": "b", "name": "B"}],
            }
            if "null-model" in sys.argv[2:]:
                response["models"]["currentModelId"] = None
            if "missing-model" in sys.argv[2:]:
                del response["models"]["currentModelId"]
        if MODE == "ui":
            update("available_commands_update", availableCommands=[{"name": "plan", "description": "Plan a task"}])
        if "usage-on-open" in FLAGS:
            update("usage_update", used=12, size=1000)
        send(id=ident, result=response)
        if "delayed-model" in sys.argv[2:]:
            sleep(0.05)
            CONFIG_OPTIONS.append(MODEL)
            update("config_option_update", configOptions=CONFIG_OPTIONS)
    elif method == "_fixture/announce_options":
        CONFIG_OPTIONS.append(MODEL)
        update("config_option_update", configOptions=CONFIG_OPTIONS)
        send(id=ident, result={})
    elif method == "session/set_model" and MODE == "legacy":
        assert params == {"sessionId": SESSION, "modelId": "b"}
        update("current_model_update", currentModelId="b")
        send(id=ident, result={})
    elif method == "_session/steering" and (MODE == "standard-steer" or "standard-steer" in FLAGS):
        assert params["sessionId"] == SESSION
        prompt_items = params["prompt"]
        STEER_PROMPTS.append(prompt_items)
        if MODE == "standard-steer":
            if prompt_items[0]["type"] == "text" and prompt_items[0]["text"] == "next turn":
                send(id=ident, result={"outcome": "promptRequired"})
            else:
                send(id=ident, result={"outcome": "injected"})
        else:
            send(id=ident, result={"outcome": "injected"})
            if PENDING is not None:
                text(json.dumps(prompt_items))
                send(id=PENDING, result={"stopReason": "end_turn"})
                PENDING = None
    elif method == "session/set_config_option" and MODE == "ui":
        option = next(item for item in CONFIG_OPTIONS if item["id"] == params["configId"])
        assert params["value"] in {choice["value"] for choice in option["options"]}, params
        rejected = "unconfirmed-model-change" in FLAGS and option is MODEL and params["value"] == "b"
        option["currentValue"] = None if rejected else params["value"]
        if "usage-on-model-change" in FLAGS and option is MODEL and not rejected:
            update("usage_update", used=5, size=2000)
        send(id=ident, result={"configOptions": CONFIG_OPTIONS})
        if "late-model-on-mode-change" in FLAGS and MODEL not in CONFIG_OPTIONS and MODEL_GATE.exists():
            CONFIG_OPTIONS.append(MODEL)
            update("config_option_update", configOptions=CONFIG_OPTIONS)
    elif method == "_fixture/discover":
        assert {entry["name"] for entry in mcp("tools/list")["tools"]} == set(CATALOG)
        assert mcp("tools/call", {"name": "identify"}, expect_error=True)["data"]["code"] == "acp_run_inactive"
        send(id=ident, result={})
    elif method == "session/prompt":
        raw_blocks = params["prompt"]
        blocks = [item["text"] for item in raw_blocks if item.get("type") == "text"]
        if MODE == "load":
            assert blocks in (["wait"], ["again"], ["tools"]), blocks
        if MODE == "new-no-system":
            assert blocks in (["guide\n\n", "first"], ["again"]), blocks
        message = (
            next(block for block in reversed(blocks) if not block.startswith("This input was submitted through "))
            if MODE == "ui"
            else blocks[-1]
        )
        if MODE == "ui" and any(block.strip() == "echo-instructions" for block in blocks):
            text(json.dumps(SYSTEM_PROMPT))
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and any(block.strip().startswith("echo-prompt") for block in blocks):
            text(json.dumps(raw_blocks))
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and any(block.strip() == "compact-and-echo" for block in blocks):
            update("compaction_update", compactionId="compact-1", status="completed")
            text(json.dumps(raw_blocks))
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and any(block.strip() == "wait-steer" for block in blocks):
            text("waiting")
            PENDING = ident
            continue
        if MODE == "ui" and any(block.strip() == "call-screenshot" for block in blocks):
            tool = mcp("tools/call", {"name": "screenshot", "arguments": {}})
            text(json.dumps(tool))
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message == "disconnect-prompt":
            break
        if MODE == "ui" and message == "verify-restored-model":
            assert MODEL["currentValue"] == "b"
            text("Model B restored")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message in {"reject-prompt", "fail-after-progress", "fail-after-accept", "fail-after-tool"}:
            if message == "fail-after-progress":
                text("Turn started")
            elif message == "fail-after-accept":
                turn_id = params["_meta"]["dev.a13n.harness-ui"]["turnId"]
                send(
                    method="_a13n/input_accepted",
                    params={"sessionId": params["sessionId"], "turnId": turn_id, "inputId": "fixture"},
                )
            elif message == "fail-after-tool":
                posted = mcp(
                    "tools/call",
                    {
                        "name": "post_to_inbox",
                        "arguments": {"kind": "result", "title": "Tool before error", "message": "Saved."},
                    },
                )
                assert not posted["isError"], posted
            send(id=ident, error={"code": -32603, "message": "Fixture rejected the prompt"})
            continue
        if MODE == "ui" and message.startswith("observe"):
            update("session_info_update", title=message.partition(":")[2] or "Peer title")
            update("usage_update", used=10, size=1000)
            update("usage_update", used=24, size=1000)
            text("peer observed")
            send(
                id=ident,
                result={
                    "stopReason": "end_turn",
                    "usage": {
                        "inputTokens": 8,
                        "outputTokens": 2,
                        "cachedReadTokens": 5,
                        "thoughtTokens": 3,
                        "totalTokens": 18,
                    },
                },
            )
            continue
        if MODE == "ui" and any(block.startswith("echo-prior:") for block in blocks):
            prior_match = next(
                (
                    match.group(0)
                    for block in blocks
                    if (match := re.search(r"<prior-thread-conversation>.*?</prior-thread-conversation>", block, re.S))
                ),
                None,
            )
            text(f"session={SESSION};mode={CONFIG_OPTIONS[0]['currentValue']};prior={prior_match or 'none'}")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message in {"handoff-check", "handoff-next"}:
            prior_match = next(
                (
                    match.group(0)
                    for block in blocks
                    if (match := re.search(r"<prior-thread-conversation>.*?</prior-thread-conversation>", block, re.S))
                ),
                None,
            )
            assert bool(prior_match) == (message == "handoff-check"), blocks
            if prior_match:
                assert "native-visible" in prior_match and "native answer" in prior_match
                assert "private system text" not in prior_match
            text("verified handoff")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if message == "cancel-me":
            text("partial reply")
            PENDING = ident
            continue
        if MODE == "ui" and message == "goal":
            text("Checking the work.")
            update("compaction_update", compactionId="compact-1", status="completed")
            text("\n[GOAL_COMPLETE]")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message == "goal-failed-compaction":
            text("Checking the work.")
            update("compaction_update", compactionId="compact-fail", status="failed", error="quota_exceeded")
            update("compaction_update", compactionId="compact-cancel", status="cancelled")
            update("compaction_update", compactionId="compact-wip", status="in_progress")
            text("\n[GOAL_COMPLETE]")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message == "compact-live":
            update("usage_update", used=800, size=1000)
            update("compaction_update", compactionId="c-1", status="in_progress")
            update("compaction_summary_chunk", compactionId="c-1", content={"type": "text", "text": "Part 1. "})
            update("compaction_summary_chunk", compactionId="c-1", content={"type": "text", "text": "Part 2."})
            update("compaction_update", compactionId="c-1", status="completed")
            text("Done after compact.")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message == "compact-then-usage":
            update("usage_update", used=800, size=1000)
            update("compaction_update", compactionId="c-2", status="completed")
            update("usage_update", used=150, size=1000)
            text("Refreshed after compact.")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message == "compact-cancelled":
            update("compaction_update", compactionId="c-cancel", status="cancelled")
            text("Cancelled compaction.")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and message == "usage-then-fail":
            update("usage_update", used=420, size=1000)
            send(id=ident, error={"code": -32603, "message": "Failed after usage"})
            continue
        if MODE == "ui" and "acp_compaction" in message:
            text("Audit complete.\n[GOAL_COMPLETE]")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if MODE == "ui" and "files" in sys.argv[2:]:
            contents = [file_content(block) for block in blocks if extract_file_path(block) is not None]
            assert contents == ["initial line\n" * 80, "name,value\nexample,42\n"], contents
            text("Files read")
            PENDING = ident
            continue
        if MODE == "ui" and message in {"inbox exact", "inbox fallback"}:
            if "browser" in sys.argv[2:]:
                catalog = {entry["name"] for entry in mcp("tools/list")["tools"]}
                assert {"browser_observe", "browser_notify_on_handback"} <= catalog, catalog
            if message == "inbox exact":
                update("tool_call", toolCallId="inbox-exact", title="Post update", name="harness-host/post_to_inbox")
            posted = mcp(
                "tools/call",
                {"name": "post_to_inbox", "arguments": {"kind": "result", "title": message, "message": "Saved."}},
            )
            assert not posted["isError"], posted
            if message == "inbox exact":
                update("tool_call_update", toolCallId="inbox-exact", status="completed")
        if MODE == "ui" and message == "Show the workspace review plan":
            update(
                "plan",
                entries=[
                    {"content": "Inspect the workspace", "priority": "high", "status": "completed"},
                    {"content": "Review changed files", "priority": "high", "status": "in_progress"},
                    {"content": "Summarize findings", "priority": "medium", "status": "pending"},
                ],
            )
            update("usage_update", used=4096, size=32768)
            text("I inspected the workspace. Next I will review the changed files and summarize the findings.")
            send(id=ident, result={"stopReason": "end_turn"})
            continue
        if message == "questions":
            update(
                "tool_call", toolCallId="question", title="Choose a format", name="mcp__harness-host__ask_user_question"
            )
            tool = mcp(
                "tools/call",
                {
                    "name": "ask_user_question",
                    "arguments": {
                        "questions": [
                            {
                                "header": "Format",
                                "question": "Which format?",
                                "multiSelect": False,
                                "options": [
                                    {"label": "JSON", "description": "Structured data"},
                                    {"label": "Text", "description": "Plain text"},
                                ],
                            }
                        ]
                    },
                },
            )
            assert not tool["isError"], tool
            update("tool_call_update", toolCallId="question", status="completed")
            text(json.loads(tool["content"][0]["text"])["answers"]["Which format?"])
            send(id=ident, result={"stopReason": "end_turn"})
            continue
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
        if MODE == "ui" and message == "worker task":
            catalog = {entry["name"] for entry in mcp("tools/list")["tools"]}
            assert (
                "send_thread_message" in catalog and "create_worker" not in catalog and "post_to_inbox" not in catalog
            )
        if MODE == "ui" and message == "review callbacks":
            options = [{"name": "Allow", "optionId": "allow", "kind": "allow_once"}]
            update(
                "tool_call", toolCallId="review-execute", title="Test", kind="execute", rawInput={"command": "denied"}
            )
            outcome = session_call(
                "session/request_permission",
                toolCall={"toolCallId": "review-execute"},
                options=options,
            )
            assert outcome["outcome"]["outcome"] == "cancelled", outcome
            denied = callback("terminal/create", sessionId=SESSION, command="denied")
            assert denied["error"]["data"]["code"] == "acp_command_denied", denied
            terminal = session_call("terminal/create", command=sys.executable, args=["-c", "print('reviewed')"])[
                "terminalId"
            ]
            assert session_call("terminal/wait_for_exit", terminalId=terminal)["exitCode"] == 0
            assert "reviewed" in session_call("terminal/output", terminalId=terminal)["output"]
            session_call("terminal/release", terminalId=terminal)
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
        if MCP and MODE != "ui":
            assert {entry["name"] for entry in mcp("tools/list")["tools"]} == set(CATALOG)

            def call_tool(**meta):
                tool = mcp("tools/call", {"name": "identify", "arguments": {}}, **meta)
                text(tool["content"][0]["text"])

            for key, name in (
                ("one", "mcp__harness-host__identify"),
                ("exact", "identify"),
                ("two", "harness-host/identify"),
            ):
                update("tool_call", toolCallId=key, title="Identify", name=name, status="in_progress")
                call_tool(**({"toolCallId": "explicit"} if key == "two" and MODE == "remote" else {}))
                if key == "exact":
                    update("tool_call_update", toolCallId=key, status="completed")
            call_tool()
            for key in ("one", "two"):
                update("tool_call_update", toolCallId=key, status="completed")
        update("user_message_chunk", content={"type": "text", "text": "HIDDEN ECHO"})
        text("done:" + message)
        send(id=ident, result={"stopReason": "end_turn"})
    elif method == "_a13n/steer":
        assert PENDING is not None
        if "files" in sys.argv[2:]:
            assert file_content(params["text"]) == "steered line\n" * 80
            send(id=ident, result={"delivery": "accepted"})
            text("Steering file read")
            send(id=PENDING, result={"stopReason": "end_turn"})
            PENDING = None
        else:
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
