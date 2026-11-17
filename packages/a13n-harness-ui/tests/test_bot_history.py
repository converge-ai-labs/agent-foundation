"""Bot control and publication reads stay independent of native checkpoint size."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInput, BotMessage, BotSubmit
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage.checkpoint_lock import checkpoint_lock
from a13n_harness_ui.webui import create_webui
from anyio import fail_after
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings
from .test_bot import _write_configuration, ready

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def test_bot_snapshot_settlement_and_indexed_pages_do_not_open_checkpoint(tmp_path, monkeypatch):
    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            yield "A visible final response."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(
        settings, configuration_path=_write_configuration(tmp_path), host_mode="webui"
    ) as app:
        bot = await ready(app)
        async with bot._settlement_lock:
            item = await bot.submit(BotSubmit(input_id="checkpoint-free", text="Keep the control plane responsive"))
            assert (await app.wait_root_operation(item.receipt_id)).status.value == "completed"

            async def forbidden(*args, **kwargs):
                pytest.fail("Bot control and indexed history must not hydrate immutable checkpoints")

            monkeypatch.setattr(app._store.objects, "read_model", forbidden)
            async with checkpoint_lock(settings.storage.data_root, BOT_THREAD_ID):
                with fail_after(2):
                    snapshot = await bot.snapshot()
                    assert "prose" not in snapshot.model_dump()
                    assert snapshot.thread is not None
                    await bot._finish(item)
                    page = await app.get_thread_transcript(thread_id=BOT_THREAD_ID, limit=1)
                    assert len(page.entries) == 1 and page.next_cursor
                    earlier = await app.get_thread_transcript(thread_id=BOT_THREAD_ID, cursor=page.next_cursor, limit=1)
                    assert earlier.entries[0].position < page.entries[0].position
                    assert (await bot.pause(True)).state.paused
            messages = await app._store.bot.messages()
            assert [message.text for message in messages if message.role == "assistant"] == [
                "A visible final response."
            ]
            assert (await app._store.bot.get_input(item.input_id)).state == "completed"


async def test_publications_page_through_tied_timestamps_without_native_history(tmp_path):
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
    ) as app:
        bot = await ready(app)
        now = datetime.now(UTC)
        for index in range(125):
            await app._store.bot.publish(
                BotMessage(
                    message_id=f"notice-{index:03}",
                    role="assistant",
                    text=f"Notice {index}",
                    created_at=now,
                )
            )

        @asynccontextmanager
        async def factory():
            yield app

        server = create_webui(factory, api_key="test-only-key")
        async with server.router.lifespan_context(server):
            async with AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client:
                assert (await client.get("/api/bot/messages")).status_code == 401
                client.headers["Authorization"] = "Bearer test-only-key"
                before, found = None, []
                while True:
                    params = {"limit": 50, **({"before": before} if before else {})}
                    response = await client.get("/api/bot/messages", params=params)
                    assert response.status_code == 200
                    page = response.json()
                    assert 0 < len(page["messages"]) <= 50
                    assert all(not message["display_ids"] for message in page["messages"])
                    found[0:0] = [message["message_id"] for message in page["messages"]]
                    before = page["next_cursor"]
                    if before is None:
                        break
                assert found == [f"notice-{index:03}" for index in range(125)]
                assert (await client.get("/api/bot/messages?limit=101")).status_code == 422
                assert (await client.get("/api/bot/messages?before=missing")).status_code >= 400
        assert len((await bot.snapshot()).messages) == 100


async def test_visible_result_is_not_limited_to_latest_publication_page(tmp_path):
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
    ) as app:
        await ready(app)
        now = datetime.now(UTC)
        await app._store.bot.retain(
            BotInput(
                input_id="active",
                source="user",
                text="Work",
                state="dispatching",
                created_at=now,
            )
        )
        await app._store.bot.publish(
            BotMessage(
                message_id="first-result",
                role="assistant",
                text="Verified",
                input_id="active",
                created_at=now,
            )
        )
        for index in range(110):
            await app._store.bot.publish(
                BotMessage(
                    message_id=f"later-{index:03}",
                    role="assistant",
                    text="Other input",
                    created_at=now,
                )
            )
        assert await app._store.bot.has_visible_result()


@pytest.mark.parametrize("fail_first_publication", [False, True])
async def test_terminal_multisegment_text_is_published_once_from_producer(
    tmp_path, monkeypatch, fail_first_publication
):
    from pydantic_ai.models.function import FunctionStreamedResponse

    from .test_bot import consumed

    original = FunctionStreamedResponse._map_item

    def multipart(self, item):
        if item == "First.Second.":
            yield from self._parts_manager.handle_text_delta(vendor_part_id="first", content="First.")
            yield from self._parts_manager.handle_text_delta(vendor_part_id="second", content="Second.")
        else:
            yield from original(self, item)

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            yield "First.Second."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(FunctionStreamedResponse, "_map_item", multipart)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
    ) as app:
        bot = await ready(app)
        publish = bot.publish
        failed = False

        async def transient_failure(**kwargs):
            nonlocal failed
            if fail_first_publication and not failed and kwargs.get("display_ids"):
                failed = True
                raise RuntimeError("Transient publication failure")
            return await publish(**kwargs)

        monkeypatch.setattr(bot, "publish", transient_failure)
        await bot.submit(BotSubmit(input_id="multipart", text="Respond with two text segments"))
        assert (await consumed(app, "multipart")).state == "completed"
        messages = [message for message in (await bot.snapshot()).messages if message.role == "assistant"]
        assert [message.text for message in messages] == ["First.", "Second."]
        assert all(len(message.display_ids) == 1 for message in messages)
        assert len({message.display_ids[0] for message in messages}) == 2


async def test_failed_terminal_publication_interrupts_input_but_saves_native_history(tmp_path, monkeypatch):
    from .test_bot import consumed

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            yield "Saved native response."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
    ) as app:
        bot = await ready(app)
        publish = bot.publish

        async def unavailable(**kwargs):
            if kwargs.get("display_ids"):
                raise RuntimeError("Publication unavailable")
            return await publish(**kwargs)

        monkeypatch.setattr(bot, "publish", unavailable)
        item = await bot.submit(BotSubmit(input_id="publication-failure", text="Keep the native result"))
        assert (await consumed(app, item.input_id)).state == "interrupted"
        operation = await app.wait_root_operation(item.receipt_id)
        assert operation.status.value == "failed"
        assert (await bot.snapshot()).state.last_error
        transcript = await app.get_thread_transcript(thread_id=BOT_THREAD_ID, limit=100)
        assert any(part.text == "Saved native response." for entry in transcript.entries for part in entry.parts)


def test_publication_index_upgrade_preserves_messages_and_is_repeatable(tmp_path):
    from a13n_harness_ui.storage.migration import DatabaseMigrator
    from alembic import command
    from sqlalchemy import inspect, text

    migrator = DatabaseMigrator(tmp_path / "metadata.sqlite3")
    migrator._run(lambda config: command.upgrade(config, "2a7cffc5be82"), write=True)
    message = BotMessage(message_id="before-upgrade", role="assistant", text="Retained", created_at=datetime.now(UTC))

    def seed(config):
        DatabaseMigrator._connection(config).execute(
            text("INSERT INTO bot_message (message_id, created_at, payload) VALUES (:id, :created, :payload)"),
            {"id": message.message_id, "created": message.created_at.isoformat(), "payload": message.model_dump_json()},
        )

    migrator._run(seed, write=True)
    migrator.upgrade()
    migrator.upgrade()

    def verify(config):
        connection = DatabaseMigrator._connection(config)
        assert connection.execute(text("SELECT payload FROM bot_message")).scalar_one() == message.model_dump_json()
        indexes = inspect(connection).get_indexes("bot_message")
        assert any(
            index["name"] == "ix_bot_message_created_id" and index["column_names"] == ["created_at", "message_id"]
            for index in indexes
        )
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT message_id FROM bot_message "
                "WHERE (created_at, message_id) < ('9999', 'last') "
                "ORDER BY created_at DESC, message_id DESC LIMIT 51"
            )
        ).all()
        assert any("SEARCH" in str(row) and "ix_bot_message_created_id" in str(row) for row in plan)

    migrator._run(verify)
