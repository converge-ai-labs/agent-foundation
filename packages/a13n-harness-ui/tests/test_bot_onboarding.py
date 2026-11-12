from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot import WORKSPACE_INTRODUCTION
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInput, BotMessage, BotSubmit
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage.continuations import read_continuation
from anyio import create_task_group, fail_after, sleep
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_app import _write_configuration as base_configuration
from .test_bot import _write_configuration, consumed, ready
from .test_bot_inbox import policy

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.fixture
def scripted_bot(monkeypatch):
    calls = []

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            calls.append((thread_id, messages, info))
            yield "I found the configured workspace. What would you like to work on next?"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    return calls


async def test_startup_sends_one_visible_ordinary_user_input_and_keeps_heartbeat_clock(
    tmp_path: Path, scripted_bot
) -> None:
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        item = await consumed(app, WORKSPACE_INTRODUCTION.input_id)
        assert item.state == "completed", item.error
        assert item.source == "user" and item.text == WORKSPACE_INTRODUCTION.text
        assert item.receipt_id is not None
        snapshot = await bot.snapshot()
        assert snapshot.state.activated
        assert [(message.role, message.text) for message in snapshot.messages] == [
            ("user", WORKSPACE_INTRODUCTION.text),
            ("assistant", "I found the configured workspace. What would you like to work on next?"),
        ]
        assert len(scripted_bot) == 1
        assert WORKSPACE_INTRODUCTION.text in str(scripted_bot[0][1])
        due = snapshot.state.next_heartbeat_at
        assert due is not None and due > datetime.now(UTC) + timedelta(minutes=50)
        await bot.introduce_workspace()
        await policy(app, root, enabled=False)
        await policy(app, root, enabled=True)
        await bot.introduce_workspace()
        assert len(scripted_bot) == 1
        await bot.submit(BotSubmit(input_id="next-chat", text="Tell me more"))
        assert (await consumed(app, "next-chat")).state == "completed"
        assert len(scripted_bot) == 2
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.introduce_workspace()
        assert len(scripted_bot) == 2
        assert len((await bot.snapshot()).inputs) == 2


@pytest.mark.parametrize("missing_model", [False, True])
async def test_first_usable_enable_starts_introduction_even_with_heartbeat_off(
    tmp_path: Path, scripted_bot, missing_model
) -> None:
    root = base_configuration(tmp_path)
    if missing_model:
        root.write_text(
            root.read_text().replace("agent: agent-assistant", "agent: null") + "webui:\n  bot:\n    enabled: true\n"
        )
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.introduce_workspace()
        assert not scripted_bot and not (await bot.snapshot()).inputs
        await policy(app, root, enabled=True, model="model-primary", heartbeat_enabled=False)
        with fail_after(5):
            while not await app._store.bot.get_input(WORKSPACE_INTRODUCTION.input_id):
                await sleep(0.01)
        assert (await consumed(app, WORKSPACE_INTRODUCTION.input_id)).state == "completed"
        assert len(scripted_bot) == 1
        assert (await bot.snapshot()).state.next_heartbeat_at is None


@pytest.mark.parametrize("existing", ["activated", "input", "message", "manual"])
async def test_existing_bot_activity_suppresses_introduction(tmp_path: Path, scripted_bot, existing) -> None:
    root = base_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        if existing == "activated":
            state = await app._store.bot.state()
            await app._store.bot.save_state(state.model_copy(update={"activated": True}))
        elif existing == "input":
            await app._store.bot.retain(
                BotInput(
                    input_id="older-input",
                    source="user",
                    text="Older input",
                    state="discarded",
                    created_at=datetime.now(UTC),
                )
            )
        elif existing == "message":
            await app._store.bot.publish(
                BotMessage(
                    message_id="legacy-message", role="assistant", text="Saved history", created_at=datetime.now(UTC)
                )
            )
        if existing == "manual":
            # Hold automatic activation while the first real human message establishes history.
            await bot.pause(True)
        await policy(app, root, enabled=True)
        if existing == "manual":
            await bot.submit(BotSubmit(input_id="human-first", text="My actual first question"))
            await consumed(app, "human-first")
            await bot.pause(False)
        await bot.introduce_workspace()
        assert await app._store.bot.get_input(WORKSPACE_INTRODUCTION.input_id) is None
        assert len(scripted_bot) == (1 if existing == "manual" else 0)


async def test_paused_and_pending_decision_hold_introduction(tmp_path: Path, scripted_bot, monkeypatch) -> None:
    from types import SimpleNamespace

    root = base_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.pause(True)
        await policy(app, root, enabled=True)
        await bot.introduce_workspace()
        assert not scripted_bot
        await app._threads.bot_thread()
        async with bot._lock:
            state = await app._store.bot.state()
            await app._store.bot.save_state(state.model_copy(update={"paused": False}))
            monkeypatch.setattr(
                bot._projections, "detail", AsyncMock(return_value=SimpleNamespace(deferred_requests=["decision"]))
            )
        await bot.introduce_workspace()
        assert not scripted_bot and not await app._store.bot.inputs()


async def test_immediate_thread_report_cannot_skip_first_visible_introduction(tmp_path: Path, scripted_bot) -> None:
    root = base_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        working = await app.create_thread(title="Existing working Thread")
        await policy(app, root, enabled=True)
        await bot.receive_thread_message(
            source_thread_id=working.thread_id,
            source_thread_title=working.title,
            text="A substantive saved result is ready for inspection.",
            kind="report",
        )
        assert (await consumed(app, WORKSPACE_INTRODUCTION.input_id)).state == "completed"
        inputs = await app._store.bot.inputs()
        assert inputs[0].input_id == WORKSPACE_INTRODUCTION.input_id
        assert inputs[0].source == "user"
        assert any(message.text == WORKSPACE_INTRODUCTION.text for message in await app._store.bot.messages())


async def test_concurrent_introduction_attempts_share_one_native_submission(tmp_path: Path, scripted_bot) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        async with create_task_group() as group:
            for _ in range(5):
                group.start_soon(bot.introduce_workspace)
        assert (await consumed(app, WORKSPACE_INTRODUCTION.input_id)).state == "completed"
        assert len(scripted_bot) == 1
        assert len(await app._store.bot.inputs()) == 1


@pytest.mark.parametrize("failure", ["admission", "provider"])
async def test_failed_introduction_is_visible_and_never_automatically_replayed(
    tmp_path: Path, scripted_bot, monkeypatch, failure
) -> None:
    root = base_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    attempts = []
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        if failure == "admission":

            async def reject(**kwargs):
                attempts.append(kwargs)
                raise ThreadError("Scripted admission failure", code="scripted_failure")

            monkeypatch.setattr(bot._runs, "submit_prompt", reject)
        else:

            async def unavailable(self, model_id, *, thread_id):
                attempts.append(thread_id)
                raise ValueError("Scripted provider failure")

            monkeypatch.setattr(HarnessUiModelResolver, "resolve", unavailable)
        await policy(app, root, enabled=True)
        # Policy acceptance wakes automation immediately; observe its durable outcome.
        item = await consumed(app, WORKSPACE_INTRODUCTION.input_id)
        assert item.state == "interrupted"
        assert len(attempts) == 1
        # Admission records interruption before introduction's finally publishes the message.
        with fail_after(5):
            async with bot._lock:
                assert [m.text for m in await app._store.bot.messages() if m.role == "user"] == [
                    WORKSPACE_INTRODUCTION.text
                ]
        await bot.pause(False)
        await bot.introduce_workspace()
        await policy(app, root, enabled=False)
        await policy(app, root, enabled=True)
        await bot.introduce_workspace()
        assert len(attempts) == 1
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.introduce_workspace()
        assert len(attempts) == 1


@pytest.mark.parametrize("memory_enabled", [False, True])
async def test_bot_uses_native_private_memory_and_restores_scope_cursors(
    tmp_path: Path, monkeypatch, memory_enabled
) -> None:
    root = _write_configuration(tmp_path)
    root.write_text(
        root.read_text() + f"memory:\n  enabled: {str(memory_enabled).lower()}\n  auto_organize:\n    enabled: false\n"
    )
    directory = tmp_path / "memory/projects/project-main"
    directory.mkdir(parents=True)
    (directory / "MEMORY.md").write_text("Source Project memory stays unchanged")
    step = 0

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal step
            names = {tool.name for tool in info.function_tools}
            if not memory_enabled:
                assert "memory_file_create" not in names
                yield "Memory is disabled; I can still discover the current workspace."
                return
            assert "memory_file_create" in names
            assert "Private to Bot" in str(info.instructions)
            plan = (
                ("read_project_memory", {"project_id": "project-main", "path": "MEMORY.md"}),
                (
                    "memory_file_create",
                    {
                        "memory": "bot",
                        "path": "MEMORY.md",
                        "content": "Workspace map: project-main. Source: Project memory; purpose unverified.",
                    },
                ),
                ("memory_file_view", {"memory": "bot", "path": "MEMORY.md"}),
            )
            if step < len(plan):
                name, arguments = plan[step]
                step += 1
                yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"memory-step-{step}")}
                return
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            assert any("Workspace map: project-main" in str(part.content) for part in returns)
            yield "The workspace map is saved with source references."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        await ready(app)
        item = await consumed(app, WORKSPACE_INTRODUCTION.input_id)
        assert item.state == "completed", item.error
        thread = await app._threads.get(BOT_THREAD_ID)
        stored = await read_continuation(app._store.objects, thread.continuation)
        assert ("bot" in stored.memory_cursors) == memory_enabled
        assert (directory / "MEMORY.md").read_text() == "Source Project memory stays unchanged"
        assert (tmp_path / "memory/bot/MEMORY.md").exists() == memory_enabled
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="remember", text="Recover the workspace map"))
        assert (await consumed(app, "remember")).state == "completed"
