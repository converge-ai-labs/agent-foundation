"""Isolated ACP subprocess and JSON-RPC helpers for wire tests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

PROFILE = "dev.a13n.harness-ui"


_FAKE = """
import asyncio
import json
from functools import partial
from pathlib import Path
import a13n_harness.models.codex as codex
from a13n_harness.capabilities.tool_review import ToolReviewAssessment, ToolReviewResult
from a13n_harness.tools.permissions import ToolPermissionsCapability
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.messages import ToolCallPart, ToolReturnPart, UserPromptPart
from a13n_harness_ui.cli import main

async def review(self, request, *, context):
    return ToolReviewResult(assessment=ToolReviewAssessment(risk="extra_high", reason="flagged by test reviewer"))

ToolPermissionsCapability.review = review

async def stream(model, messages, info):
    prompts = [str(p.content) for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
    if any("model check" in p for p in prompts):
        yield f"{model} {info.model_request_parameters.thinking}"
        return
    if any("shell check" in p for p in prompts):
        Path('shell-tool-surface').write_text(json.dumps(sorted(t.name for t in info.function_tools)))
        Path('shell-instructions').write_text(info.instructions or '')
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "shell_exec"]
        if not returns:
            command = f"printf '%s' ran > '{Path.cwd() / 'shell-marker'}'"
            yield {0: DeltaToolCall(name="shell_exec", json_args=json.dumps({"command": command}))}
            return
        result = str(returns[0].content)
        if "denied in unattended mode" in result:
            yield "denied automatically"
        elif returns[0].outcome == "denied":
            yield "denied by client"
        else:
            yield "command finished"
        return
    if any("wait here" in p for p in prompts) and not any("steer now" in p for p in prompts):
        yield "waiting"
        await asyncio.sleep(30)
    tool_names = {t.name for t in info.function_tools}
    if any("show tools" in p for p in prompts):
        assert "post_to_inbox" not in tool_names, "WebUI Inbox tool was exposed in ACP"
    if any("show tools" in p for p in prompts) and "opt_echo" in tool_names:
        assert "opt_forbidden" not in tool_names, "Explicit ask escaped pre-exposure filtering"
        if not any(isinstance(p, ToolCallPart) and p.tool_name == 'opt_echo' for m in messages for p in m.parts):
            yield {0: DeltaToolCall(name="opt_echo", json_args="{}")}
            return
        if any(isinstance(p, ToolReturnPart) and "tool returned" in str(p.content) for m in messages for p in m.parts):
            yield "MCP worked"
            return
    yield "final answer"

codex.CodexRequestModel = lambda name, **kw: FunctionModel(
    stream_function=partial(stream, name), profile={"supports_thinking": True}
)
main()
"""


class Rpc:
    def __init__(self, process: asyncio.subprocess.Process, stdin_peer: socket.socket | None = None):
        self.process = process
        self.stdin_peer = stdin_peer

    async def send_bytes(self, data: bytes) -> None:
        if self.stdin_peer is not None:
            await asyncio.get_running_loop().sock_sendall(self.stdin_peer, data)
        else:
            assert self.process.stdin is not None
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    def shutdown_input(self) -> None:
        if self.stdin_peer is not None:
            self.stdin_peer.shutdown(socket.SHUT_WR)
        else:
            assert self.process.stdin is not None
            self.process.stdin.transport.abort()

    async def send(self, method, *, id=None, **params):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        if id is not None:
            msg["id"] = id
        await self.send_bytes(json.dumps(msg).encode() + b"\n")

    async def receive(self):
        assert self.process.stdout is not None
        line = await asyncio.wait_for(self.process.stdout.readline(), 45)
        assert line, "Unexpected EOF on the ACP pipe"
        return json.loads(line)

    async def response(self, id):
        frames = []
        async with asyncio.timeout(60):
            while (frame := await self.receive()).get("id") != id:
                frames.append(frame)
                assert len(frames) <= 256
        return frame, frames


@asynccontextmanager
async def _agent(
    path: Path,
    tmp_path: Path,
    *,
    unattended: bool = False,
    hooks: str = "",
    discard_stdout: bool = False,
    init_meta: dict[str, str] | None = None,
    stdin_sockets: tuple[socket.socket, socket.socket] | None = None,
):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _FAKE.removesuffix("main()\n") + hooks + "\nmain()\n",
        "--config",
        str(path),
        "--data-root",
        str(tmp_path / "data"),
        "acp",
        *(("--unattended",) if unattended else ()),
        env=os.environ.copy(),
        cwd=tmp_path,
        stdin=stdin_sockets[1] if stdin_sockets else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=8192 if discard_stdout else 65536,
    )
    if stdin_sockets:
        stdin_sockets[1].close()
        stdin_sockets[0].setblocking(False)
    assert process.stderr and process.stdout
    stderr = asyncio.create_task(process.stderr.read())
    try:
        rpc = Rpc(process, stdin_sockets[0] if stdin_sockets else None)
        metadata = dict(init_meta or {})
        params = {"_meta": metadata} if metadata else {}
        await rpc.send("initialize", id=1, protocolVersion=1, clientCapabilities={}, **params)
        result, frames = await rpc.response(1)
        assert not frames
        assert result["result"]["protocolVersion"] == 1
        profile = result["result"]["_meta"][PROFILE]
        assert profile == {"version": 1, "unattended": unattended}
        assert "resume" in result["result"]["agentCapabilities"]["sessionCapabilities"]
        yield rpc
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if stdin_sockets and process.returncode is None:
            stdin_sockets[0].close()
        stdout = asyncio.create_task(process.stdout.read()) if discard_stdout else None
        try:
            await asyncio.wait_for(process.wait(), 30)
        except TimeoutError:
            process.kill()
            await process.wait()
        if stdout is not None:
            await stdout
        diagnostics = (await stderr).decode()
        if sys.exc_info()[0] is not None:
            print(diagnostics)
        rpc.diagnostics = diagnostics
        assert process.returncode == 0, diagnostics
        assert not await process.stdout.read(), "Unexpected trailing stdout"


async def _wait_for_marker(path: Path, *, timeout: float = 45) -> None:
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.02)
