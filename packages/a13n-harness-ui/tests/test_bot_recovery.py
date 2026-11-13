from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from a13n_harness import mcp as client_module
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotSubmit
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import ObjectKind
from a13n_harness_ui.surfaces import RootOperationStatus
from anyio import Event, sleep_forever
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
            operation = await app.wait_root_operation(failed.receipt_id)
            assert operation.status is RootOperationStatus.failed
            assert operation.outcome is None
            assert operation.failure.code == "root_operation_failed"
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
                assert "saved Bot Thread execution failure" in prompts[0]
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


@pytest.mark.parametrize("run_status", ["preparation_failed", "completed", "failed", "cancelled"])
async def test_terminal_summary_save_failure_pauses_bot(tmp_path, monkeypatch, run_status):
    root = _write_configuration(tmp_path)
    started = Event()
    calls = 0

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal calls
            calls += 1
            started.set()
            if run_status == "cancelled":
                await sleep_forever()
            if run_status == "failed":
                raise RuntimeError("Model unavailable")
            yield "Completed before the summary save failed"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        save = app._root_runs._save_execution
        assert save is not None

        async def fail_preparation(composition):
            raise RuntimeError("Environment preparation unavailable")

        if run_status == "preparation_failed":
            monkeypatch.setattr(app._root_runs._executor._environments, "prepare", fail_preparation)

        async def fail_terminal_summary(thread_id, execution):
            if execution.completed_at is not None:
                assert execution.status == ("failed" if run_status == "preparation_failed" else run_status)
                raise OSError("Execution summary unavailable")
            return await save(thread_id, execution)

        monkeypatch.setattr(app._root_runs, "_save_execution", fail_terminal_summary)
        command = BotSubmit(input_id="unknown-summary", text="Do this once")
        async with bot._settlement_lock:
            admitted = await bot.submit(command)
            if run_status == "cancelled":
                await started.wait()
                await app.cancel_root_operation(admitted.receipt_id)
            operation = await app.wait_root_operation(admitted.receipt_id)
            assert operation.status is RootOperationStatus.failed
            assert operation.failure.code == "execution_status_save_failed"
            if run_status == "preparation_failed":
                assert operation.run_id is None and operation.outcome is None
            elif run_status != "cancelled":
                # A selected continuation alone cannot prove durable settlement.
                assert operation.outcome.execution.status == run_status
                assert operation.outcome.continuation.status == "selected"
            durable = (await app._threads.get(BOT_THREAD_ID)).last_execution
            assert durable.execution_id == admitted.receipt_id
            assert durable.status == ("preparing" if run_status == "preparation_failed" else "running")
            assert durable.completed_at is None
            await bot._finish(admitted)
        interrupted = await consumed(app, command.input_id)
        snapshot = await bot.snapshot()
        assert interrupted.state == "interrupted" and interrupted.error
        assert snapshot.state.paused and snapshot.state.last_error
        clock = snapshot.state.next_heartbeat_at
        assert clock is not None
        # Repairing storage grants neither input replay nor an automatic wake.
        monkeypatch.setattr(app._root_runs, "_save_execution", save)
        assert await bot.submit(command) == interrupted
        await deliver_tick(bot, now=clock + timedelta(seconds=1))
        assert (await bot.snapshot()).inputs == (interrupted,)
        assert calls == (0 if run_status == "preparation_failed" else 1)


@pytest.mark.parametrize("failure_stage", ["publish", "select"])
@pytest.mark.parametrize("run_status", ["startup_failed", "completed", "failed"])
async def test_terminal_continuation_failure_pauses_bot(tmp_path, monkeypatch, failure_stage, run_status):
    root = _write_configuration(tmp_path)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    model_finished = False
    calls = 0

    async def resolve(self, model_id, *, thread_id):
        nonlocal model_finished
        if run_status == "startup_failed":
            model_finished = True
            raise RuntimeError("Model initialization unavailable")

        async def model(messages, info):
            nonlocal model_finished, calls
            calls += 1
            model_finished = True
            if run_status == "failed":
                raise RuntimeError("Model unavailable")
            yield "Completed before the continuation save failed"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        publish = app._store.objects.publish_model
        select = app._store.threads.select_continuation
        prior = None

        async def fail_terminal_publish(**kwargs):
            if model_finished and kwargs["object_kind"] == ObjectKind.continuation_state:
                raise OSError("Continuation publication unavailable")
            return await publish(**kwargs)

        async def fail_terminal_select(**kwargs):
            nonlocal prior
            if model_finished:
                raise OSError("Continuation selection unavailable")
            selected = await select(**kwargs)
            prior = kwargs["replacement"]
            return selected

        if failure_stage == "publish":
            monkeypatch.setattr(app._store.objects, "publish_model", fail_terminal_publish)
        monkeypatch.setattr(app._store.threads, "select_continuation", fail_terminal_select)
        command = BotSubmit(input_id="unknown-continuation", text="Do this once")
        async with bot._settlement_lock:
            admitted = await bot.submit(command)
            operation = await app.wait_root_operation(admitted.receipt_id)
            assert operation.status is RootOperationStatus.failed
            if run_status == "startup_failed":
                assert operation.outcome is None
                assert operation.failure.code == "continuation_selection_failed"
                assert "Diagnostic report:" in operation.failure.message
                assert any(
                    "Model initialization unavailable" in report.read_text()
                    for report in tmp_path.glob("a13n-harness-ui-error-*.json")
                )
                assert prior is None
            else:
                assert operation.failure is None
                assert operation.outcome.execution.status == run_status
                assert operation.outcome.continuation.status == "failed"
                assert operation.outcome.continuation.failure.code == "continuation_selection_failed"
                assert prior is not None
            assert (await app._threads.get(BOT_THREAD_ID)).continuation == prior
            durable = (await app._threads.get(BOT_THREAD_ID)).last_execution
            assert durable.status == "failed" and durable.completed_at is not None
            await bot._finish(admitted)
        interrupted = await consumed(app, command.input_id)
        snapshot = await bot.snapshot()
        assert interrupted.state == "interrupted" and interrupted.error
        assert snapshot.state.paused and snapshot.state.last_error
        clock = snapshot.state.next_heartbeat_at
        assert clock is not None
        monkeypatch.setattr(app._store.objects, "publish_model", publish)
        monkeypatch.setattr(app._store.threads, "select_continuation", select)
        assert await bot.submit(command) == interrupted
        await deliver_tick(bot, now=clock + timedelta(seconds=1))
        assert (await bot.snapshot()).inputs == (interrupted,)
        assert calls == (0 if run_status == "startup_failed" else 1)


async def test_settled_native_failure_recovers_on_fresh_heartbeat(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    available = False
    calls = 0
    prompts = []

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal calls
            calls += 1
            prompts.append(str(messages))
            if not available:
                raise RuntimeError("Model temporarily unavailable")
            yield "Recovered on a fresh opportunity"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        command = BotSubmit(input_id="settled-failure", text="Do this once")
        async with bot._settlement_lock:
            admitted = await bot.submit(command)
            operation = await app.wait_root_operation(admitted.receipt_id)
            assert operation.status is RootOperationStatus.failed
            assert operation.run_id is not None and operation.failure is None
            assert operation.outcome.execution.status == "failed"
            assert operation.outcome.continuation.status == "selected"
            await bot._finish(admitted)
        interrupted = await consumed(app, command.input_id)
        state = (await bot.snapshot()).state
        assert interrupted.state == "interrupted" and state.last_error
        assert not state.paused
        clock = state.next_heartbeat_at
        assert clock is not None
        assert await bot.submit(command) == interrupted
        assert calls == 1
        available = True
        await deliver_tick(bot, now=clock + timedelta(seconds=1))
        snapshot = await bot.snapshot()
        assert snapshot.inputs[0] == interrupted
        assert len(snapshot.inputs) == 2 and calls == 2
        assert snapshot.inputs[-1].source == "automatic" and snapshot.inputs[-1].state == "completed"
        assert snapshot.inputs[-1].receipt_id != interrupted.receipt_id
        assert not snapshot.state.paused and snapshot.state.last_error is None
        assert snapshot.state.next_heartbeat_at > clock
        assert "fresh opportunity, not a replay" in prompts[-1]
