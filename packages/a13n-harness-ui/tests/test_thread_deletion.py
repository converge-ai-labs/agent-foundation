from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import HarnessUiError, StoreConflictError, ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage.database import short_session, transaction
from a13n_harness_ui.storage.models import ChildExecutionRecord, DeletedThreadRecord, ShareGrantRecord
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus, ThreadMetadataMutation, ThreadMetadataPatch
from a13n_harness_ui.thread_files import AttachmentUpload, ThreadFiles
from anyio import Event, fail_after, sleep
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import select

from .test_app import _settings, _write_configuration
from .test_builtin_subagents import _source
from .test_sharing_webui import create_link, shared_server
from .test_tool_executions import install_slow_tool

pytestmark = pytest.mark.anyio


async def test_delete_removes_archived_chat_files_and_shares_but_preserves_fork(tmp_path, monkeypatch):
    path = _write_configuration(tmp_path)

    async def resolve(self, context, model_id):
        async def model(messages, info):
            yield "A saved response"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        thread = await app.create_thread(title="Delete me")
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Saved question")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        attachment = await app._thread_files.stage(thread.thread_id, AttachmentUpload("note.txt", b"keep in fork"))
        directory = app._thread_files.directory(thread.thread_id)
        outside = tmp_path / "outside.txt"
        outside.write_text("keep outside")
        (directory / "external").symlink_to(outside)
        copied = await app.fork_thread(thread.thread_id)
        draft = await app.shared_draft(thread.thread_id)
        thread = await app.update_thread_metadata(
            thread_id=thread.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=thread.metadata_version, patch=ThreadMetadataPatch(archived=True)
            ),
        )
        assert await app.delete_thread(thread_id=thread.thread_id, expected_version=thread.metadata_version) == (
            thread.thread_id,
        )
        assert not directory.exists() and outside.read_text() == "keep outside"
        assert draft.closed
        with pytest.raises(ThreadError, match=r"not found|does not exist"):
            await app.get_thread(thread.thread_id)
        history = json.dumps((await app.get_thread_transcript(thread_id=copied.thread_id)).model_dump(mode="json"))
        assert "Saved question" in history and "A saved response" in history
        assert (await app._thread_files.read(copied.thread_id, attachment.attachment_id))[1] == b"keep in fork"
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert await app._store.threads.get(thread.thread_id) is None
        assert (await app.get_thread(copied.thread_id)).thread.thread_id == copied.thread_id


async def test_delete_family_handles_resumed_segments_and_checks_active_descendants(tmp_path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        root = await app.create_thread()
        saved = await app._threads.get(root.thread_id)
        store = app._store
        now = datetime.now(UTC)
        parent = root.thread_id
        for child in ("thread-child", "thread-grandchild"):
            await store.threads.create(
                thread_id=child,
                parent_thread_id=parent,
                configuration=saved.configuration,
                initial_state=saved.initial_state,
            )
            async with transaction(store.database.sessions) as session:
                for segment in range(3):
                    session.add(
                        ChildExecutionRecord(
                            execution_id=f"{child}-{segment}",
                            parent_thread_id=parent,
                            child_thread_id=child,
                            child_run_id=f"run-{child}-{segment}",
                            segment_index=segment,
                            run_composition_schema_version="1",
                            run_composition_digest="a" * 64,
                            status="succeeded",
                            resumed_from=f"{child}-{segment - 1}" if segment else None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    await session.flush()
            parent = child
        async with transaction(store.database.sessions) as session:
            session.add(
                ShareGrantRecord(
                    share_id="share-delete",
                    scope="thread",
                    target_id=root.thread_id,
                    permission="view",
                    token_digest="b" * 64,
                    created_at=now,
                    expires_at=now,
                )
            )
        with pytest.raises(StoreConflictError, match="subagents"):
            await store.threads.delete(
                root.thread_id,
                expected_version=1,
                active_execution_ids=frozenset({"thread-grandchild-2"}),
                busy_thread_ids=frozenset(),
            )
        with pytest.raises(StoreConflictError, match="subagents"):
            await store.threads.delete(
                root.thread_id,
                expected_version=1,
                active_execution_ids=frozenset(),
                busy_thread_ids=frozenset({"thread-child"}),
            )
        async with short_session(store.database.sessions) as session:
            assert not list(await session.scalars(select(DeletedThreadRecord)))
        deleted = await app.delete_thread(thread_id=root.thread_id, expected_version=1)
        assert set(deleted) == {root.thread_id, "thread-child", "thread-grandchild"}
        async with short_session(store.database.sessions) as session:
            assert not list(await session.scalars(select(ChildExecutionRecord)))
            assert not list(await session.scalars(select(ShareGrantRecord)))
            assert set(await session.scalars(select(DeletedThreadRecord.thread_id))) == set(deleted)
        assert all([await store.threads.get(identity) is None for identity in deleted])


async def test_delete_rejects_stale_versions_and_coordinator_with_workers(tmp_path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        coordinator = await app.create_thread(coordinator=True)
        worker = await app.create_thread(coordinator_thread_id=coordinator.thread_id)
        with pytest.raises(StoreConflictError, match="worker chats first"):
            await app.delete_thread(thread_id=coordinator.thread_id, expected_version=1)
        with pytest.raises(StoreConflictError, match="changed"):
            await app.delete_thread(thread_id=worker.thread_id, expected_version=9)
        assert await app._store.threads.get(worker.thread_id) is not None
        await app.delete_thread(thread_id=worker.thread_id, expected_version=1)
        await app.delete_thread(thread_id=coordinator.thread_id, expected_version=1)
        assert not await app._store.threads.coordinators()


async def test_delete_rejects_active_root(tmp_path, monkeypatch):
    path = _write_configuration(tmp_path)
    started, release = Event(), Event()

    async def resolve(self, context, model_id):
        async def model(messages, info):
            started.set()
            await release.wait()
            yield "Finished"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        try:
            with fail_after(10):
                await started.wait()
            with pytest.raises(HarnessUiError) as failure:
                await app.delete_thread(thread_id=thread.thread_id, expected_version=1)
            assert failure.value.code == "thread_run_active"
            assert await app._store.threads.get(thread.thread_id) is not None
        finally:
            release.set()
            await app.wait_root_operation(receipt.receipt_id)


async def test_delete_waits_for_child_completion_delivery_and_resumed_run(tmp_path, monkeypatch):
    path = _source(tmp_path, ("explorer",))
    path.write_text(path.read_text() + "tools:\n  execution: {mode: auto, foreground_wait_seconds: 0}\n")
    finish, delivery_entered, delivery_allowed, resumed, release = (Event() for _ in range(5))
    stages = {"parent": 0, "child": 0}

    async def slow() -> int:
        await finish.wait()
        return 42

    install_slow_tool(monkeypatch, slow)

    async def model(messages, info):
        lane = "parent" if "delegate" in {tool.name for tool in info.function_tools} else "child"
        stage = stages[lane]
        stages[lane] += 1
        if lane == "parent":
            if stage == 0:
                yield {
                    0: DeltaToolCall(
                        name="delegate",
                        json_args='{"subagent_name":"explorer","prompt":"Work"}',
                        tool_call_id="delegate",
                    )
                }
            elif stage == 1:
                yield {0: DeltaToolCall(name="wait_subagent", json_args='{"timeout_seconds":10}', tool_call_id="wait")}
            else:
                yield "Parent finished"
        elif stage == 0:
            yield {0: DeltaToolCall(name="run_code", json_args='{"code":"await slow()"}', tool_call_id="runner")}
        elif stage == 1:
            yield "Child waiting"
        else:
            resumed.set()
            await release.wait()
            yield "Child resumed"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        original_delivery = app._tool_executions.deliver

        async def gated_delivery(thread_id, prompt):
            delivery_entered.set()
            await delivery_allowed.wait()
            return await original_delivery(thread_id, prompt)

        app._tool_executions.deliver = gated_delivery
        thread = await app.create_thread(defaults=NewThreadDefaults(agent_id="agent-main"))
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Delegate")
        with fail_after(15):
            try:
                assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
                child_id = (
                    (await app.query_child_executions(parent_thread_id=thread.thread_id)).executions[0].child_thread_id
                )
                finish.set()
                await delivery_entered.wait()
                while app._tool_executions.runtime.active:
                    await sleep(0)
                # The tool has finished, but its in-flight hint can still resume the child.
                assert not await app._subagent_operator.active_execution_ids()
                assert child_id not in app._tool_executions._pending
                assert child_id in app._tool_executions._scheduled
                with pytest.raises(StoreConflictError) as failure:
                    await app.delete_thread(thread_id=thread.thread_id, expected_version=thread.metadata_version)
                assert failure.value.code == "thread_run_active"
                assert await app._store.threads.get(thread.thread_id) is not None
                assert await app._store.threads.get(child_id) is not None
                # Rejection releases admission so the completion can resume normally.
                delivery_allowed.set()
                await resumed.wait()
                with pytest.raises(StoreConflictError) as failure:
                    await app.delete_thread(thread_id=thread.thread_id, expected_version=thread.metadata_version)
                assert failure.value.code == "thread_run_active"
            finally:
                finish.set()
                delivery_allowed.set()
                release.set()
            while await app._subagent_operator.active_execution_ids() or app._tool_executions.busy_thread_ids():
                await sleep(0)
            deleted = await app.delete_thread(thread_id=thread.thread_id, expected_version=thread.metadata_version)
            assert set(deleted) == {thread.thread_id, child_id}


async def test_delete_http_requires_owner_and_revokes_shared_access(tmp_path):
    async with shared_server(tmp_path) as (_, app, client):
        thread = await app.create_thread()
        prefix, guest = await create_link(client, "thread", thread.thread_id, "prompt")
        url = f"/api/threads/{thread.thread_id}?expected_version=1"
        assert (await client.delete(url, headers=guest)).status_code == 401
        owner = {"Authorization": "Bearer owner-test-key"}
        assert (await client.delete(url.replace("version=1", "version=2"), headers=owner)).status_code == 409
        response = await client.delete(url, headers=owner)
        assert response.status_code == 200, response.text
        assert response.json() == [thread.thread_id]
        missing = await client.get(f"/api/threads/{thread.thread_id}", headers=owner)
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "thread_missing"
        assert (await client.get(prefix + "/access", headers=guest)).status_code == 401


async def test_delete_rejects_reserved_threads_and_independent_child(tmp_path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        root = await app.create_thread()
        saved = await app._threads.get(root.thread_id)
        child = await app._store.threads.create(
            thread_id="thread-child",
            parent_thread_id=root.thread_id,
            configuration=saved.configuration,
            initial_state=saved.initial_state,
        )
        live = await app._threads.live_thread(configuration_root=path.parent)
        memory = await app._threads.memory_thread(scope="global", project_id=None, model_id="model-test")
        for thread, code in (
            (child, "child_thread_scoped"),
            (live, "live_thread_reserved"),
            (memory, "memory_thread_read_only"),
        ):
            with pytest.raises(ThreadError) as failure:
                await app.delete_thread(thread_id=thread.thread_id, expected_version=1)
            assert failure.value.code == code
            assert await app._store.threads.get(thread.thread_id) is not None


async def test_deleted_files_respect_other_app_use_and_preserve_symlink_targets(tmp_path):
    first = ThreadFiles(tmp_path)
    second = ThreadFiles(tmp_path)
    try:
        await first.touch("thread-shared")
        await second.touch("thread-shared")
        directory = first.directory("thread-shared")
        await first.remove("thread-shared")
        assert directory.exists()
        await second.close()
        await first.remove("thread-shared")
        assert not directory.exists()
        outside = tmp_path / "outside"
        outside.mkdir()
        directory.symlink_to(outside, target_is_directory=True)
        await first.remove("thread-shared")
        assert outside.is_dir() and not directory.is_symlink()
    finally:
        await first.close()
        await second.close()
