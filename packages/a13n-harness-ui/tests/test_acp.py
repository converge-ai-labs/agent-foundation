"""ACP session and client interoperability contract tests."""

from __future__ import annotations

import asyncio
import sys

import pytest

from .acp_test_support import _FAKE, PROFILE, _in_process_agent
from .test_interactive import _seed

pytestmark = pytest.mark.usefixtures("migrated_databases")


def test_acp_rejects_windows_before_app_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.cli as cli_module
    from click.testing import CliRunner

    def unexpected_start(request):
        raise AssertionError("ACP must not start an App on Windows")

    monkeypatch.setattr(cli_module, "system", lambda: "Windows")
    monkeypatch.setattr(cli_module, "_execute", unexpected_start)
    result = CliRunner().invoke(cli_module.cli, ["acp"])
    assert result.exit_code == 1
    assert not result.stdout
    assert result.stderr == "Error: ACP over stdio is not supported on Windows.\n"


@pytest.mark.anyio
async def test_acp_rejects_nonordinary_resume_and_dangling_model(tmp_path):
    from types import SimpleNamespace
    from typing import cast

    from a13n_harness_ui.acp_server import AcpAgent
    from a13n_harness_ui.app import HarnessUiApp
    from a13n_harness_ui.surfaces import RunModelOverrides
    from acp import RequestError

    thread = SimpleNamespace(
        role="coordinator",
        coordinator_thread_id=None,
        parent_thread_id=None,
        archived=False,
        memory_scope=None,
        configuration=SimpleNamespace(agent_source=SimpleNamespace(kind="agent")),
    )

    class App:
        async def get_thread(self, thread_id):
            return SimpleNamespace(thread=thread)

        async def current_configuration(self):
            return SimpleNamespace(models={"available": SimpleNamespace(name="Available")})

        async def inspect_thread_configuration(self, thread_id):
            return SimpleNamespace(next_model_id="removed")

    agent = AcpAgent(cast(HarnessUiApp, App()))
    await agent.initialize(1)
    with pytest.raises(RequestError) as error:
        await agent.resume_session("session", str(tmp_path))
    assert error.value.code == -32602
    thread.role = "ordinary"
    thread.configuration.agent_source.kind = "live"
    with pytest.raises(RequestError) as error:
        await agent.resume_session("session", str(tmp_path))
    assert error.value.code == -32602
    agent.model_overrides["session"] = RunModelOverrides(model_id="also-removed")
    (model_option,) = await agent._options("session")
    assert model_option.current_value == "default"
    assert model_option.current_value in {option.value for option in model_option.options}


@pytest.mark.anyio
async def test_unknown_usage_window_reports_in_extension_notification(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    from a13n_harness_ui.app import HarnessUiApp

    original_usage = HarnessUiApp.context_usage

    async def context_usage(self, thread_id):
        context = await original_usage(self, thread_id)
        return context.model_copy(update={"context_window": None})

    monkeypatch.setattr(HarnessUiApp, "context_usage", context_usage)
    async with _in_process_agent(path, tmp_path, monkeypatch) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send("session/prompt", id=3, sessionId=session_id, prompt=[{"type": "text", "text": "hello"}])
        reply, frames = await rpc.response(3)
        assert reply["result"]["stopReason"] == "end_turn", reply
        updates = [
            frame["params"]["update"]["sessionUpdate"] for frame in frames if frame.get("method") == "session/update"
        ]
        assert "notice" not in updates and "usage_update" not in updates
        reports = [frame["params"] for frame in frames if frame.get("method") == "_a13n/usage_report"]
        assert reports
        for report in reports:
            assert report["version"] == 1 and report["sessionId"] == session_id and report["usageReport"]["records"]


@pytest.mark.anyio
async def test_new_prompt_acceptance_stream_and_resume(tmp_path, monkeypatch):
    path = await _seed(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    model_path = path.parent / "models" / "codex.yaml"
    model_source = model_path.read_text()
    (model_path.parent / "second.yaml").write_text(
        model_source.replace("model-codex", "model-second").replace("gpt-5.6-sol", "gpt-5.6-luna")
    )
    async with _in_process_agent(path, tmp_path, monkeypatch) as rpc:
        await rpc.send("session/new", id=2, cwd=str(tmp_path), mcpServers=[])
        reply, _ = await rpc.response(2)
        session_id = reply["result"]["sessionId"]
        await rpc.send(
            "session/prompt",
            id=3,
            sessionId=session_id,
            prompt=[{"type": "text", "text": "hello"}],
            _meta={PROFILE: {"inputIds": ["abc"]}},
        )
        reply, frames = await rpc.response(3)
        assert reply.get("result", {}).get("stopReason") == "end_turn", reply
        final = reply["result"]["_meta"][PROFILE]
        assert final["finalOutput"] == "final answer"
        assert final["finalOutputOmitted"] is False
        assert len(final["finalMessage"]["continuationId"]) == 64
        assert final["finalMessage"]["position"] > 0
        assert any(
            frame["method"] == "_a13n/input_accepted" and frame["params"]["inputId"] == "abc" for frame in frames
        )
        assert any(
            frame["method"] == "session/update"
            and frame["params"]["update"].get("sessionUpdate") == "agent_message_chunk"
            for frame in frames
        )
        assert any(
            "usageReport" in f.get("params", {}).get("update", {}).get("_meta", {}).get(PROFILE, {}) for f in frames
        )
        await rpc.send("_a13n/steer", id=4, sessionId=session_id, turnId="first", inputId="late", text="too late")
        reply, _ = await rpc.response(4)
        assert reply["result"]["delivery"] == "not_delivered"
        await rpc.send("_a13n/inventory", id=5, sessionId=session_id)
        reply, _ = await rpc.response(5)
        assert reply["result"]["defaultModel"] == "model-codex"
        assert {model["id"] for model in reply["result"]["models"]} == {"model-codex", "model-second"}
        for id, option, value in [(6, "model", "model-second"), (7, "thought_level", "low")]:
            await rpc.send("session/set_config_option", id=id, sessionId=session_id, configId=option, value=value)
            reply, _ = await rpc.response(id)
            assert any(o["id"] == option and o["currentValue"] == value for o in reply["result"]["configOptions"])
        await rpc.send("session/prompt", id=8, sessionId=session_id, prompt=[{"type": "text", "text": "model check"}])
        reply, _ = await rpc.response(8)
        assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "gpt-5.6-luna low"
    async with _in_process_agent(path, tmp_path, monkeypatch) as rpc:
        await rpc.send("session/resume", id=2, sessionId=session_id, cwd=str(other), mcpServers=[])
        reply, _ = await rpc.response(2)
        assert "error" in reply
        await rpc.send("session/resume", id=3, sessionId=session_id, cwd=str(tmp_path), mcpServers=[])
        reply, frames = await rpc.response(3)
        assert "result" in reply and not frames
        assert {option["id"]: option["currentValue"] for option in reply["result"]["configOptions"]} == {
            "model": "model-codex",
            "thought_level": "high",
        }
        await rpc.send("session/prompt", id=4, sessionId=session_id, prompt=[{"type": "text", "text": "model check"}])
        reply, _ = await rpc.response(4)
        assert reply["result"]["_meta"][PROFILE]["finalOutput"] == "gpt-5.6-sol high"
        assert reply["result"]["_meta"][PROFILE]["finalMessage"]["position"] > final["finalMessage"]["position"]
    assert model_source == model_path.read_text()


@pytest.mark.anyio
async def test_harness_acp_client_interoperates_with_ui_agent(tmp_path, monkeypatch):
    from a13n_harness.acp import AcpRunBinding, AcpSessionRuntime, AcpSessionSetup, LocalStdioTransport
    from acp.schema import AgentMessageChunk, TextContentBlock

    path = await _seed(tmp_path, monkeypatch)
    transport = LocalStdioTransport(
        sys.executable,
        str(tmp_path),
        ("-c", _FAKE, "--config", str(path), "--data-root", str(tmp_path / "data"), "acp"),
        inherit_environment=True,
    )
    setup = AcpSessionSetup(str(tmp_path))
    async with asyncio.timeout(45):
        async with AcpSessionRuntime.open(transport) as runtime:
            assert await runtime.open_session(setup)
            updates = []
            response = await runtime.prompt(
                [TextContentBlock(text="model check")], binding=AcpRunBinding(on_update=updates.append)
            )
            assert response.stop_reason == "end_turn"
            assert "gpt-5.6-sol high" in "".join(
                update.content.text
                for update in updates
                if isinstance(update, AgentMessageChunk) and isinstance(update.content, TextContentBlock)
            )
