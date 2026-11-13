from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from a13n_harness import mcp as client_module
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BotSubmit
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from fastmcp import Client, FastMCP
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings
from .test_bot import _write_configuration, consumed, deliver_tick, ready
from .test_tool_search_integration import _http_mcp

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.mark.parametrize("host_owned", [False, True])
@pytest.mark.parametrize("paused", [False, True])
async def test_failed_mcp_run_preserves_heartbeat_and_later_recovers(tmp_path: Path, monkeypatch, host_owned, paused):
    root = _write_configuration(tmp_path)
    original = Client._negotiate
    attempts = 0
    available = False
    prompts = []

    async def negotiate(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if not available:
            try:
                raise TimeoutError("MCP temporarily unavailable")
            except TimeoutError as exc:
                raise RuntimeError("Failed to initialize server session") from exc
        return await original(self, *args, **kwargs)

    async def backoff(delay):
        await asyncio.sleep(0)

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            prompts.append(str(messages))
            yield "Recovered on a fresh opportunity"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(Client, "_negotiate", negotiate)
    monkeypatch.setattr(client_module, "sleep", backoff)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with _http_mcp(FastMCP("Bot recovery")) as url:
        directory = tmp_path / "mcp"
        directory.mkdir()
        (directory / "test.yaml").write_text(
            f'schema_version: "1"\nkind: mcp_server\nid: mcp-test\nname: Test\non_error: raise\ntransport:\n  url: {url}\n'
        )
        with (tmp_path / "agents" / "assistant.yaml").open("a") as stream:
            stream.write("mcp_servers: [mcp-test]\n")
        if host_owned:
            with root.open("a") as stream:
                stream.write("mcp:\n  host_owned_servers: [mcp-test]\n")
        async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
            bot = await ready(app)
            if paused:
                await bot.pause(True)
            await bot.submit(BotSubmit(input_id="failed", text="Inspect ongoing work"))
            failed = await consumed(app, "failed")
            assert failed.state == "interrupted"
            assert (await app.wait_root_operation(failed.receipt_id)).status.value == "failed"
            state = (await bot.snapshot()).state
            assert state.paused is paused
            assert state.last_error
            assert attempts == 3 and not prompts
            clock = state.next_heartbeat_at
            assert clock is not None
            # Failed execution is settled; the accepted input is never replayed.
            assert (await bot.submit(BotSubmit(input_id="failed", text="Inspect ongoing work"))) == failed
            assert attempts == 3
            available = True
            await deliver_tick(bot, now=clock + timedelta(seconds=1))
            if paused:
                assert attempts == 3
                assert (await bot.snapshot()).state.paused
                await bot.pause(False)
                await bot.deliver_automatic_inputs()
                inputs = await app._store.bot.inputs()
                await consumed(app, inputs[-1].input_id)
            snapshot = await bot.snapshot()
            assert len(snapshot.inputs) == 2
            assert snapshot.inputs[0] == failed
            assert snapshot.inputs[-1].source == "automatic"
            assert snapshot.inputs[-1].state == "completed"
            assert snapshot.state.last_error is None
            assert snapshot.state.next_heartbeat_at > clock
            assert attempts == 4 and len(prompts) == 1
            if not paused:
                assert "fresh opportunity, not a replay" in prompts[0]
            # Success does not create an immediate retry loop or reset the clock.
            await deliver_tick(bot, now=clock + timedelta(seconds=1))
            assert len((await bot.snapshot()).inputs) == 2


@pytest.mark.parametrize("host_owned", [False, True])
async def test_bot_default_skip_completes_current_run_and_retries_at_next_heartbeat(tmp_path, monkeypatch, host_owned):
    root = _write_configuration(tmp_path)
    available = False
    attempts = 0
    instructions = []
    original = Client._negotiate

    async def negotiate(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if not available:
            raise TimeoutError("MCP temporarily unavailable")
        return await original(self, *args, **kwargs)

    async def backoff(delay):
        await asyncio.sleep(0)

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            instructions.append(str(messages) + str(info.instructions))
            yield "Completed with available capabilities"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(Client, "_negotiate", negotiate)
    monkeypatch.setattr(client_module, "sleep", backoff)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with _http_mcp(FastMCP("Bot optional MCP")) as url:
        directory = tmp_path / "mcp"
        directory.mkdir()
        (directory / "test.yaml").write_text(
            f'schema_version: "1"\nkind: mcp_server\nid: mcp-test\nname: Test\ntransport:\n  url: {url}\n'
        )
        with (tmp_path / "agents" / "assistant.yaml").open("a") as stream:
            stream.write("mcp_servers: [mcp-test]\n")
        if host_owned:
            with root.open("a") as stream:
                stream.write("mcp:\n  host_owned_servers: [mcp-test]\n")
        async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
            bot = await ready(app)
            await bot.submit(BotSubmit(input_id="optional", text="Inspect ongoing work"))
            first = await consumed(app, "optional")
            assert first.state == "completed"
            snapshot = await bot.snapshot()
            assert not snapshot.state.paused and snapshot.state.last_error is None
            assert attempts == 3 and "mcp-test is unavailable" in instructions[0]
            clock = snapshot.state.next_heartbeat_at
            assert clock is not None
            available = True
            await deliver_tick(bot, now=clock + timedelta(seconds=1))
            inputs = await app._store.bot.inputs()
            second = await consumed(app, inputs[-1].input_id)
            assert second.source == "automatic" and second.state == "completed"
            assert attempts == 4 and len(instructions) == 2
            assert (await bot.snapshot()).state.next_heartbeat_at > clock
