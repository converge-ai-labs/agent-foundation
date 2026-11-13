from __future__ import annotations

from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.thread_capability import ThreadToolController, _thread_input
from anyio import fail_after, sleep

from .test_app import _CompletedReconstructor, _settings, _write_configuration
from .test_bot import ready
from .test_bot_inbox import policy

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def controller(app):
    return ThreadToolController(
        projections=app._projections,
        threads=app._store.threads,
        root_runs=app._root_runs,
        create_thread=app.create_thread,
        configurations=app._configurations,
        bot=app.bot(),
    )


async def test_thread_report_retains_host_provenance_while_off_and_delivers_on_enabled_opportunity(tmp_path: Path):
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        source = await app.create_thread(title="Release work")
        tools = controller(app)
        accepted = await tools.send_thread_message(
            source_thread_id=source.thread_id,
            thread_id=BOT_THREAD_ID,
            kind="report",
            message="Verified result; please integrate.",
        )
        assert accepted["ok"] and accepted["mode"] == "inbox"
        batch = await app._store.bot_inbox.pending()
        assert len(batch) == 1 and batch[0].source == "thread"
        assert batch[0].source_thread_id == source.thread_id
        assert batch[0].source_thread_title == "Release work"
        assert batch[0].event_type == "report"
        await bot.deliver_automatic_inputs()
        assert not (await bot.snapshot()).thread
        assert await app._store.bot_inbox.pending() == batch
        # Pause before enabling: configuration publication signals immediate delivery.
        await bot.pause(True)
        app._root_runs._executor._agents = _CompletedReconstructor()
        await policy(app, root, enabled=True)
        await bot.deliver_automatic_inputs()
        assert await app._store.bot_inbox.pending() == batch
        await bot.pause(False)
        await bot.deliver_automatic_inputs()
        assert not await app._store.bot_inbox.pending()
        assert (await bot.snapshot()).inputs[0].source == "automatic"
        with pytest.raises(HarnessUiError):
            await tools.send_thread_message(source_thread_id=BOT_THREAD_ID, thread_id=BOT_THREAD_ID, message="self")


async def test_thread_report_notifies_existing_loop_without_waiting_for_period(tmp_path: Path):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  bot: {enabled: true, connector_interval_seconds: 86400}\n")
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        app._root_runs._executor._agents = _CompletedReconstructor()
        source = await app.create_thread()
        await controller(app).send_thread_message(
            source_thread_id=source.thread_id, thread_id="bot", kind="report", message="A blocker needs attention"
        )
        with fail_after(5):
            while not (await bot.snapshot()).inputs or await app._store.bot_inbox.pending():
                await sleep(0.01)
        assert not await app._store.bot_inbox.pending()


async def test_bot_return_address_is_hidden_and_shared_by_all_thread_input_paths(tmp_path: Path):
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        await app._threads.bot_thread()
        source = (await app.get_thread(BOT_THREAD_ID)).thread
        for context in (None, "Created objective"):
            hidden, visible = _thread_input(source, "Do the bounded task", context=context)
            assert hidden.metadata["display"] is False
            assert "thread_bot" in hidden.content
            assert "kind='report'" in hidden.content
            assert "saved final response" in hidden.content
            assert visible.content == "Do the bounded task"
            assert visible.metadata["harness_ui"]["thread_message"]["source_thread_id"] == BOT_THREAD_ID


async def test_own_note_context_is_changed_only_including_empty_and_disabled(tmp_path: Path, monkeypatch):
    import json

    from a13n_harness.content import request_input_content
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    root = _write_configuration(tmp_path)
    seen = []
    plan = [
        ("write_bot_note", {"content": "A"}),
        ("read_bot_note", {}),
        ("write_bot_note", {"content": "B"}),
        ("write_bot_note", {"content": "A"}),
        ("clear_bot_note", {}),
    ]

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            blocks = {item.metadata.source_id: item.value for item in request_input_content(messages[-1])}
            seen.append(blocks)
            step = len(seen) - 1
            if step == len(plan):
                yield "Saved handoff"
            else:
                name, args = plan[step]
                yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"note-{step}")}

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Maintain handoff")
        assert (await app.wait_root_operation(receipt.receipt_id)).status.value == "completed"
        status_id, note_id = "a13n-harness-ui.bot-collaboration", "a13n-harness-ui.bot-note"
        assert 'enabled="false"' in seen[0][status_id]
        assert "No Bot Note." in seen[0][note_id]
        assert ">\nA\n<" in seen[1][note_id]
        assert note_id not in seen[2] and status_id not in seen[2]
        assert ">\nB\n<" in seen[3][note_id]
        assert ">\nA\n<" in seen[4][note_id]
        assert "No Bot Note." in seen[5][note_id]
        assert not (await bot.snapshot()).thread  # Context and note writes never wake Bot.


@pytest.mark.parametrize("silent", [False, True])
async def test_saved_bot_prose_survives_tools_and_silent_completion_with_one_publication_per_segment(
    tmp_path: Path, monkeypatch, silent
):
    from a13n_harness_ui.bot_models import BotSubmit
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_bot import consumed

    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  bot: {enabled: true}\n")
    step = 0

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal step
            step += 1
            if step == 1:
                yield "I will inspect the working Threads."
                yield {0: DeltaToolCall(name="list_threads", json_args="{}", tool_call_id="inspect")}
            elif silent:
                yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet")}
            else:
                yield "Verified final result."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="prose", text="Check ongoing work"))
        assert (await consumed(app, "prose")).state == "completed"
        snapshot = await bot.snapshot()
        assert [prose.text for prose in snapshot.prose] == [
            "I will inspect the working Threads.",
            *([] if silent else ["Verified final result."]),
        ]
        assert len(snapshot.messages) == 1 + len(snapshot.prose)  # User input plus each visible segment once.
        publications = [message for message in snapshot.messages if message.role == "assistant"]
        assert [(message.text, message.display_ids) for message in publications] == [
            (prose.text, (prose.item_id,)) for prose in snapshot.prose
        ]
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        restored = await (await ready(app)).snapshot()
        assert restored.prose == snapshot.prose
        assert restored.messages == snapshot.messages


async def test_note_http_views_are_read_only_and_observational(tmp_path: Path):
    from contextlib import asynccontextmanager

    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        thread = await app.create_thread(title="Ongoing work")
        await app._store.bot_notes.write(caller=thread.thread_id, thread_id=thread.thread_id, content="Current handoff")

        @asynccontextmanager
        async def factory():
            yield app

        server = create_webui(factory, api_key="test-only-key")
        async with server.router.lifespan_context(server):
            async with AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client:
                assert (await client.get("/api/bot/notes")).status_code == 401
                client.headers["Authorization"] = "Bearer test-only-key"
                listing = await client.get("/api/bot/notes?limit=1&offset=0")
                assert listing.status_code == 200
                assert listing.json()["notes"][0]["title"] == "Ongoing work"
                detail = await client.get(f"/api/bot/notes/{thread.thread_id}")
                assert detail.json()["content"] == "Current handoff"
                assert (
                    await client.post(f"/api/bot/notes/{thread.thread_id}", json={"content": "replace"})
                ).status_code == 405
                await app._store.bot_notes.write(caller=thread.thread_id, thread_id=thread.thread_id, content="")
                assert (await client.get("/api/bot/notes")).json()["notes"] == []
                assert (await client.get(f"/api/bot/notes/{thread.thread_id}")).json()["content"] == ""
                assert not (await bot.snapshot()).thread


async def test_oversized_thread_envelope_is_rejected_before_retention(tmp_path: Path):
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        await ready(app)
        source = await app.create_thread(title="Work")
        tools = controller(app)
        with pytest.raises(HarnessUiError) as error:
            await tools.send_thread_message(
                source_thread_id=source.thread_id, thread_id=BOT_THREAD_ID, message="😀" * 16384
            )
        assert error.value.code == "thread_message_too_large"
        assert not await app._store.bot_inbox.pending()
        await tools.send_thread_message(
            source_thread_id=source.thread_id, thread_id=BOT_THREAD_ID, message="Short report"
        )
        assert len(await app._store.bot_inbox.pending()) == 1
