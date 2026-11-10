from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness.errors import DefinitionError
from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY, HarnessToolMetadata
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.storage.database import transaction
from a13n_harness_ui.storage.models import BotNoteRecord, ThreadRecord
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability, ThreadToolController
from anyio import Event, create_task_group

from .test_app import _settings, _write_configuration
from .test_bot import ready

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def test_bot_notes_are_offline_data_with_lww_clear_archive_fork_and_delete(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        assert not (await bot.snapshot()).enabled
        a = await app.create_thread(title="Working")
        b = await app.create_thread(title="Other")
        notes = bot.notes
        assert not (await notes.read(caller=a.thread_id, thread_id=a.thread_id)).content
        first = await notes.write(
            caller=a.thread_id, thread_id=a.thread_id, content="Track release\nhttps://example.test"
        )
        assert first == await notes.write(caller=BOT_THREAD_ID, thread_id=a.thread_id, content=first.content)
        changed = await notes.write(caller=BOT_THREAD_ID, thread_id=a.thread_id, content="Follow up")
        assert changed.last_writer_thread_id == BOT_THREAD_ID
        assert changed.updated_at >= first.updated_at
        assert not (await notes.write(caller=a.thread_id, thread_id=a.thread_id, content=" \n")).content
        await notes.write(caller=BOT_THREAD_ID, thread_id=a.thread_id, content="Recreated")
        async with transaction(app._store.database.sessions) as session:
            row = await session.get(ThreadRecord, a.thread_id)
            row.archived = True
        listing = await notes.list(caller=BOT_THREAD_ID, limit=1)
        assert listing["notes"][0]["archived"]
        assert listing["notes"][0]["project_id"] == a.configuration.project_id
        async with transaction(app._store.database.sessions) as session:
            row = await session.get(ThreadRecord, a.thread_id)
            row.archived = False
        fork = await app.fork_thread(a.thread_id)
        assert not (await notes.read(caller=fork.thread_id, thread_id=fork.thread_id)).content
        await notes.write(caller=BOT_THREAD_ID, thread_id=b.thread_id, content="Other work")
        assert (await notes.list(caller=BOT_THREAD_ID, limit=1))["next_offset"] == 1
        for target in (b.thread_id, BOT_THREAD_ID, "thread-missing"):
            with pytest.raises(HarnessUiError):
                await notes.write(caller=a.thread_id, thread_id=target, content="Forbidden")
        with pytest.raises(HarnessUiError, match="16 KiB"):
            await notes.write(caller=a.thread_id, thread_id=a.thread_id, content="界" * 6000)
        with pytest.raises(HarnessUiError):
            await notes.list(caller=a.thread_id)
        for target in (BOT_THREAD_ID, "thread-missing"):
            with pytest.raises(HarnessUiError):
                await notes.read(caller=BOT_THREAD_ID, thread_id=target)
        await app.delete_thread(thread_id=a.thread_id, expected_version=a.metadata_version)
        async with transaction(app._store.database.sessions) as session:
            assert await session.get(BotNoteRecord, a.thread_id) is None
        assert not (await bot.snapshot()).inputs
        assert not (await bot.snapshot()).thread  # Notes created no Bot root or wake.


async def test_concurrent_note_mutations_follow_commit_order(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        thread = await app.create_thread()
        first_done = Event()

        async def owner():
            await bot.notes.write(caller=thread.thread_id, thread_id=thread.thread_id, content="Owner update")
            first_done.set()

        async def coordinator():
            await first_done.wait()
            await bot.notes.write(caller=BOT_THREAD_ID, thread_id=thread.thread_id, content="Bot update")

        async with create_task_group() as group:
            group.start_soon(owner)
            group.start_soon(coordinator)
        assert (await bot.notes.read(caller=thread.thread_id, thread_id=thread.thread_id)).content == "Bot update"


async def test_note_tool_schemas_bind_ordinary_roots_and_recheck_host_context(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        thread = await app.create_thread()
        controller = ThreadToolController(
            projections=app._projections,
            threads=app._store.threads,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
            configurations=app._configurations,
            bot=bot,
        )
        ordinary = ThreadCollaborationCapability(controller=controller, source_thread_id=thread.thread_id)
        tools = ordinary.get_toolset().tools
        assert "list_bot_notes" not in tools
        assert tools["read_bot_note"].function_schema.json_schema["properties"] == {}
        assert set(tools["write_bot_note"].function_schema.json_schema["properties"]) == {"content"}
        context: Any = SimpleNamespace(deps=SimpleNamespace(thread_id=thread.thread_id))
        await tools["write_bot_note"].function_schema.call({"content": "A handoff"}, context)
        wrong: Any = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-other"))
        with pytest.raises(DefinitionError):
            await ordinary.write_bot_note(wrong, "Impersonation")
        privileged = ThreadCollaborationCapability(controller=controller, source_thread_id=BOT_THREAD_ID)
        bot_tools = privileged.get_toolset().tools
        assert "list_bot_notes" in bot_tools
        for toolset in (tools, bot_tools):
            for name in ("write_bot_note", "clear_bot_note"):
                metadata = toolset[name].metadata[HARNESS_TOOL_METADATA_KEY]
                assert isinstance(metadata, HarnessToolMetadata)
                assert metadata.effects == frozenset({"write"})
                assert metadata.idempotency == "none"
            for name in ("read_bot_note", "list_bot_notes"):
                if name in toolset:
                    metadata = toolset[name].metadata[HARNESS_TOOL_METADATA_KEY]
                    assert isinstance(metadata, HarnessToolMetadata)
                    assert metadata.effects == frozenset({"read"})
                    assert metadata.idempotency == "read_only"
        assert set(bot_tools["write_bot_note"].function_schema.json_schema["properties"]) == {"thread_id", "content"}
        with pytest.raises(DefinitionError):
            await privileged.write_thread_bot_note(context, thread.thread_id, "Impersonation")
