"""Every visible Bot text segment owns one unread revision and notification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotSubmit
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.thread_capability import ThreadToolController
from anyio import Event, fail_after
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_bot import _write_configuration, consumed, ready
from .test_thread_read_state import receipt

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.mark.parametrize("ending", ["final", "silent", "cancel"])
@pytest.mark.parametrize("automatic", [False, True])
async def test_intermediate_text_notifies_before_slow_work_and_survives_terminal_outcome(
    tmp_path, monkeypatch, ending, automatic
):
    root = _write_configuration(tmp_path)
    started, release = Event(), Event()
    steps = 0
    resources = ThreadToolController.resources

    async def slow_resources(self, **kwargs):
        started.set()
        await release.wait()
        return await resources(self, **kwargs)

    async def model(messages, info):
        nonlocal steps
        step, steps = steps, steps + 1
        if step == 0:
            yield "Important "
            yield "progress before slow work."
            yield {0: DeltaToolCall(name="list_projects", json_args="{}", tool_call_id="slow-projects")}
        elif step == 1 and ending == "silent":
            yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet-after-text")}
        else:
            yield "Verified final result." if step == 1 else "Another result."

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(ThreadToolController, "resources", slow_resources)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await app._threads.bot_thread()
        notices = []
        bot._notify = lambda thread_id, notice: notices.append(notice)
        if automatic:
            await bot.tick(now=datetime.now(UTC) + timedelta(hours=1))
            await bot.deliver_automatic_inputs()
            input_id = (await app._store.bot.inputs())[-1].input_id
        else:
            input_id = "visible-prose"
            await bot.submit(BotSubmit(input_id=input_id, text="Report important progress"))
        with fail_after(10):
            await started.wait()
        assert [notice.brief for notice in notices] == ["Important progress before slow work."]
        before = await app._threads.get(BOT_THREAD_ID)
        assert before.unread and before.read_state_version == 1
        assert (await app._root_runs.active(BOT_THREAD_ID)) is not None
        if ending == "cancel":
            operation = await app._root_runs.active(BOT_THREAD_ID)
            await app.cancel_root_operation(operation.receipt.receipt_id)
            release.set()
            assert (await consumed(app, input_id)).state == "interrupted"
            assert (await app._threads.get(BOT_THREAD_ID)).unread
            assert len(notices) == 1
            return
        assert await app._store.threads.acknowledge_read((receipt(before),)) == (BOT_THREAD_ID,)
        read = await app._threads.get(BOT_THREAD_ID)
        release.set()
        assert (await consumed(app, input_id)).state == "completed"
        after = await app._threads.get(BOT_THREAD_ID)
        if ending == "silent":
            assert not after.unread and after.read_state_version == read.read_state_version
            assert len(notices) == 1
        else:
            assert after.unread and after.read_state_version == read.read_state_version + 1
            assert [notice.brief for notice in notices] == [
                "Important progress before slow work.",
                "Verified final result.",
            ]
            assert await app._store.threads.acknowledge_read((receipt(read),)) == ()
        assert len({notice.message_id for notice in notices}) == len(notices)
        # Old final display items must not be re-published under a later receipt.
        await bot.submit(BotSubmit(input_id="later", text="Next short task"))
        assert (await consumed(app, "later")).state == "completed"
        assert [notice.brief for notice in notices][-1] == "Another result."
        assert len(notices) == (2 if ending == "silent" else 3)


async def test_each_new_publication_atomically_fences_read_reports_and_duplicate_is_noop(tmp_path):
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        initial = await app._threads.bot_thread()
        notices = []
        bot._notify = lambda thread_id, notice: notices.append(notice)
        await bot.publish(message_id="first", text="First message", receipt_id="fixture")
        first = await app._threads.get(BOT_THREAD_ID)
        assert first.unread and first.read_state_version == initial.read_state_version + 1
        await bot.publish(message_id="first", text="First message", receipt_id="fixture")
        assert (await app._threads.get(BOT_THREAD_ID)).read_state_version == first.read_state_version
        await bot.publish(message_id="second", text="Second message", receipt_id="fixture")
        second = await app._threads.get(BOT_THREAD_ID)
        assert second.unread and second.read_state_version == first.read_state_version + 1
        assert await app._store.threads.acknowledge_read((receipt(first),)) == ()
        assert await app._store.threads.acknowledge_read((receipt(second),)) == (BOT_THREAD_ID,)
        assert len(notices) == 2


@pytest.mark.parametrize("ending", ["cancel", "failure"])
async def test_stopping_mid_segment_publishes_already_visible_partial_text(tmp_path, monkeypatch, ending):
    from a13n_harness_ui.root_execution import RootRunExecutor

    root = _write_configuration(tmp_path)
    visible, release = Event(), Event()
    publish_live = RootRunExecutor._publish_live

    async def observe_live(self, **kwargs):
        await publish_live(self, **kwargs)
        if any(
            item.kind == "text_message"
            and item.state == "in_progress"
            and item.content.get("text") == "Visible partial response."
            for item in kwargs["display"].items
        ):
            visible.set()

    async def model(messages, info):
        yield "Visible partial "
        yield "response."
        await release.wait()
        raise RuntimeError("Scripted failure after visible text")

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(RootRunExecutor, "_publish_live", observe_live)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await app._threads.bot_thread()
        notices = []
        bot._notify = lambda thread_id, notice: notices.append(notice)
        await bot.submit(BotSubmit(input_id="partial", text="Start responding"))
        with fail_after(10):
            await visible.wait()
        assert notices == []
        if ending == "cancel":
            operation = await app._root_runs.active(BOT_THREAD_ID)
            assert operation is not None
            await app.cancel_root_operation(operation.receipt.receipt_id)
        else:
            release.set()
        assert (await consumed(app, "partial")).state == "interrupted"
        thread = await app._threads.get(BOT_THREAD_ID)
        assert thread.unread and thread.read_state_version == 1
        assert [notice.brief for notice in notices] == ["Visible partial response."]
        messages = [message for message in (await bot.snapshot()).messages if message.role == "assistant"]
        assert [message.text for message in messages] == ["Visible partial response."]
        assert messages[0].display_ids
