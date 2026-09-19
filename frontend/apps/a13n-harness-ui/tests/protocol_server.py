"""Isolated real App for Node protocol tests; no browser or external provider."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from a13n_harness_ui import model_catalog
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel


async def main() -> None:
    with ExitStack() as stack:
        # An explicit fixture root and port allow restart recovery tests. Ordinary
        # protocol suites retain their isolated disposable directory and port.
        directory = (
            sys.argv[sys.argv.index("--root") + 1]
            if "--root" in sys.argv
            else stack.enter_context(TemporaryDirectory(prefix="a13n-webui-protocol-"))
        )
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = directory
        os.environ["USERPROFILE"] = directory
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg")
        os.environ["CODEX_HOME"] = str(root / "codex")
        (root / "codex").mkdir(exist_ok=True)
        os.environ["GROK_HOME"] = str(root / "grok")
        os.environ["GROK_AUTH_PATH"] = str(root / "grok" / "auth.json")
        if os.name == "posix":
            os.environ["SHELL"] = "/bin/sh"
            os.environ["ENV"] = str(root / "no-shell-init")
            os.environ["BASH_ENV"] = str(root / "no-shell-init")
        share_computer = "--native" in sys.argv
        native_root = root / "native"
        if share_computer:
            native_root.mkdir()
            (native_root / "sample.txt").write_bytes(b"first\r\nsecond\r\n")
            (native_root / "binary.bin").write_bytes(b"\x00\x01\xff")
            (native_root / "large.txt").write_bytes(b"x" * (512 * 1024 + 1))
            repository = native_root / "repository"
            repository.mkdir()
            git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            git_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
            for args in (
                ("init",),
                ("config", "user.name", "Protocol Fixture"),
                ("config", "user.email", "fixture@example.invalid"),
            ):
                subprocess.run(["git", *args], cwd=repository, env=git_env, check=True, capture_output=True)
            (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repository, env=git_env, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Fixture baseline"],
                cwd=repository,
                env=git_env,
                check=True,
                capture_output=True,
            )
            (repository / "tracked.txt").write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repository, env=git_env, check=True, capture_output=True)
            (repository / "tracked.txt").write_text("worktree\n", encoding="utf-8")
            (repository / "new.txt").write_text("untracked\n", encoding="utf-8")
        configuration = root / "config" / "a13n-harness-ui.yaml"
        configuration.parent.mkdir(exist_ok=True)
        setup = "--setup" in sys.argv
        if not setup:
            timeout = 120 if "--hitl" in sys.argv else 30 if "--slow" in sys.argv else 2
            configuration.write_text(
                'schema_version: "1"\ndefaults:\n  agent: agent-fixture\n'
                f"tools:\n  interaction_timeout_seconds: {timeout}\n",
                encoding="utf-8",
            )
        sources = (
            {}
            if setup
            else {
                "models/fixture.yaml": 'schema_version: "1"\nkind: model\nid: model-fixture\nname: Fixture\nroute: openai:gpt-5\nauthentication: {kind: api_key, env: FIXTURE_MODEL_KEY}\n',
                "models/alternate.yaml": 'schema_version: "1"\nkind: model\nid: model-alternate\nname: Alternate\nroute: openai:gpt-5\nauthentication: {kind: api_key, env: FIXTURE_MODEL_KEY}\n',
                "agents/fixture.yaml": 'schema_version: "1"\nkind: agent\nid: agent-fixture\nname: Fixture\nmodel: model-fixture\ncapabilities:\n  - capability: skills\n',
            }
        )
        for name, content in sources.items():
            path = configuration.parent / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(content, encoding="utf-8")

        if "--hitl" in sys.argv:
            from a13n_harness_ui.composition.reconstruction import AgentReconstructor
            from pydantic_ai.capabilities import Capability
            from pydantic_ai.tools import ToolDefinition
            from pydantic_ai.toolsets import ExternalToolset, FunctionToolset

            configuration.write_text(
                configuration.read_text()
                + "security:\n  shell_review:\n    enable: true\n    model: model-fixture\n    risk_threshold: high\n",
                encoding="utf-8",
            )
            agent_path = configuration.parent / "agents/fixture.yaml"
            agent_path.write_text(
                agent_path.read_text()
                + "  - capability: dynamic_environment\n"
                + "    configuration: {files_enabled: true, shell_enabled: true}\n",
                encoding="utf-8",
            )
            reconstruct = AgentReconstructor.reconstruct

            def with_hitl_tools(self, composition, *, root_capabilities=(), **kwargs):
                tools = FunctionToolset(id="hitl-fixture")

                @tools.tool_plain(requires_approval=True)
                def publish_fixture(value: str) -> str:
                    return f"Published: {value}"

                external = ExternalToolset(
                    [
                        ToolDefinition(
                            name="lookup_fixture", parameters_json_schema={"type": "object", "properties": {}}
                        )
                    ],
                    id="hitl-external",
                )
                return reconstruct(
                    self,
                    composition,
                    root_capabilities=(*root_capabilities, Capability(id="hitl-fixture", toolsets=(tools, external))),
                    **kwargs,
                )

            AgentReconstructor.reconstruct = with_hitl_tools

        attempts = 0

        async def model(messages, info):
            nonlocal attempts
            attempts += 1
            if "--goal" in sys.argv:
                if attempts == 1:
                    yield "Implementation complete; checking the evidence next."
                else:
                    await asyncio.sleep(1)
                    yield "Every requirement checked.\n[GOAL_COMPLETE]"
                return
            if "--execution-layout" in sys.argv:
                # Long, multi-page turns for independent conversation/execution QA.
                step = attempts % 41
                if step:
                    yield f"Step {step}: " + "Reviewing saved observations in source order. " * 12 + "\n\n"
                    yield {
                        0: DeltaToolCall(
                            name="note_write",
                            tool_call_id=f"layout-step-{attempts}",
                            json_args=json.dumps({"key": "layout", "value": f"Observed step {step}"}),
                        )
                    }
                else:
                    yield "Execution layout complete. " + "The final answer stays outside the execution reader. " * 12
                return
            if "--work" in sys.argv:
                if attempts == 1:
                    yield {
                        0: DeltaToolCall(
                            name="task_create",
                            tool_call_id="work-task",
                            json_args=json.dumps(
                                {"subject": "Review observations", "description": "Retain across Runs"}
                            ),
                        ),
                        1: DeltaToolCall(
                            name="note_write",
                            tool_call_id="work-note",
                            json_args=json.dumps({"key": "decision", "value": "Owner-published work"}),
                        ),
                    }
                else:
                    yield "Observing work. "
                    await asyncio.sleep(3)
                    yield "Complete."
                return
            if "--hitl" in sys.argv:
                if not info.function_tools:
                    yield {
                        0: DeltaToolCall(
                            name=info.output_tools[0].name,
                            json_args='{"risk":"high","reason":"Writes a fixture marker"}',
                        )
                    }
                    return
                prompts = [
                    part.content
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, UserPromptPart)
                ]
                returns = [
                    part
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                if returns:
                    yield "Handled: " + " | ".join(str(part.content) for part in returns)
                elif any("shell review" in str(prompt) for prompt in prompts):
                    marker = root / "approved-marker.txt"
                    yield {
                        0: DeltaToolCall(
                            name="shell_exec",
                            tool_call_id="shell-review",
                            json_args=json.dumps({"command": f'echo reviewed > "{marker}"'}),
                        )
                    }
                else:
                    yield {
                        0: DeltaToolCall(
                            name="publish_fixture",
                            tool_call_id="generic-approval",
                            json_args=json.dumps({"value": "draft"}),
                        ),
                        1: DeltaToolCall(name="lookup_fixture", tool_call_id="external-result", json_args="{}"),
                        2: DeltaToolCall(
                            name="ask_user_question",
                            tool_call_id="mixed-question",
                            json_args=json.dumps(
                                {
                                    "questions": [
                                        {
                                            "header": "Direction",
                                            "question": "Where?",
                                            "options": [
                                                {"label": "Left", "description": "Go left"},
                                                {"label": "Right", "description": "Go right"},
                                            ],
                                        }
                                    ]
                                }
                            ),
                        ),
                    }
                return
            if "--fail" in sys.argv and not any(
                "Continue completing the previous task." in str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ):
                raise UsageLimitExceeded("Isolated fixture failure")
            if "--retry" in sys.argv and attempts == 1:
                import httpx2

                yield "Preparing the review.\n\n"
                await asyncio.sleep(1)
                raise httpx2.ReadError("Isolated fixture interruption")
            if "--streaming-layout" in sys.argv:
                # Deterministic browser QA: repeated line/Markdown reflow without
                # a paid model, followed by the ordinary saved-history cutover.
                yield "## Streaming layout\n\n"
                for index in range(40):
                    yield f"Paragraph {index + 1}: "
                    for _ in range(4):
                        yield "The reader follows new output without restarting its connection. "
                        await asyncio.sleep(0.08)
                    yield "\n\n"
                yield "| Surface | Status |\n| --- | --- |\n| Streaming | Complete |\n\n"
                yield "Layout verification complete."
                return
            if "--rich-output" in sys.argv:
                yield "## Review result\n\nThe conversation stays readable while work continues.\n\n"
                await asyncio.sleep(2)
                yield '| Surface | Status | Notes |\n| :--- | :---: | ---: |\n| Markdown | Ready | 12 |\n| Mermaid | Ready | 3 |\n\n```python\ndef greet(name: str) -> str:\n    # Preserve the original source\n    return f"Hello, {name}"\n```\n\n'
                yield "```mermaid\nflowchart LR\n    A[Local input] --> B[Live output]\n    B --> C[Saved history]\n"
                await asyncio.sleep(1)
                yield "```\n\nAll checks are ready for human review."
                return
            if any(
                "ask a timed question" in str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ):
                replies = [
                    part
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, ToolReturnPart) and part.tool_name == "ask_user_question"
                ]
                if replies:
                    yield f"Continued: {replies[-1].content}"
                else:
                    yield {
                        0: DeltaToolCall(
                            name="ask_user_question",
                            tool_call_id="timed-question",
                            json_args=json.dumps(
                                {
                                    "questions": [
                                        {
                                            "header": "Direction",
                                            "question": "Which direction should we take?",
                                            "options": [
                                                {"label": "Left", "description": "Explore the first approach"},
                                                {"label": "Right", "description": "Explore the second approach"},
                                            ],
                                        }
                                    ]
                                }
                            ),
                        )
                    }
                return
            yield "Protocol "
            if any(
                "wait for skill inspection" in str(part.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ):
                # The protocol test cancels this Run after inspecting active steering.
                await asyncio.Event().wait()
            await asyncio.sleep(6 if "--slow" in sys.argv else 0.4)
            yield "response"

        async def resolve(self, context, model_id):
            return FunctionModel(stream_function=model)

        async def offline_directory():
            return model_catalog.bundled_models()

        model_catalog.fetch_directory = offline_directory
        HarnessUiModelResolver.__call__ = resolve
        settings = HarnessUiSettings(storage=StorageSettings(data_root=root / "data"), pricing_auto_update=False)
        server = create_webui(
            lambda: open_harness_ui_app(
                settings,
                configuration_path=configuration,
                host_mode="webui",
                share_computer=share_computer,
                instrumentation=None,
            ),
            api_key="test-only-key",
            static_root=(
                Path(sys.argv[sys.argv.index("--static-root") + 1])
                if "--static-root" in sys.argv
                else Path(__file__).resolve().parents[1] / "dist"
            ),
        )
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 0
        sock.bind(("127.0.0.1", port))
        native = uvicorn.Server(
            uvicorn.Config(
                server, log_level="error", lifespan="on", ws="websockets-sansio", timeout_graceful_shutdown=2
            )
        )
        task = asyncio.create_task(native.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not native.started:
                    if task.done():
                        await task
                        raise RuntimeError("Protocol listener stopped before startup")
                    await asyncio.sleep(0.01)
            print(
                json.dumps(
                    {
                        "origin": f"http://127.0.0.1:{sock.getsockname()[1]}",
                        "native_root": str(native_root.resolve()),
                        "fixture_root": str(root.resolve()),
                    }
                ),
                flush=True,
            )
            await asyncio.to_thread(sys.stdin.readline)
        finally:
            native.should_exit = True
            async with asyncio.timeout(10):
                await task
            sock.close()


if __name__ == "__main__":
    asyncio.run(main())
