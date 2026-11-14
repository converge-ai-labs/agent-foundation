"""Child progress checkpoints use the ordinary transcript and live-cutover contracts."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.storage import ObjectKind, StoredChildCheckpoint
from a13n_harness_ui.webui import create_webui
from anyio import Event, fail_after, sleep
from pydantic_ai.models.function import DeltaToolCall

from .test_dynamic_subagents import new_thread, open_app, returned

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@asynccontextmanager
async def child_app(tmp_path, monkeypatch, child_model):
    actions, receipts = [], []

    async def model(messages, info):
        if "send_parent_message" in {tool.name for tool in info.function_tools}:
            async for value in child_model(messages, info):
                yield value
        elif actions:
            name, arguments = actions.pop(0)
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments))}
        else:
            receipts.append(returned(messages)[-1].content)
            yield "Parent finished."

    async with await open_app(tmp_path, monkeypatch, model) as app:
        # These tests drive the parent explicitly; an automatic result copy
        # would wake it between submissions.
        app._tool_executions.deliver_subagent = None
        parent = await new_thread(app)

        async def submit(name="spawn_subagent", **arguments):
            arguments = arguments or {"name": "worker", "instructions": "CHILD_ROLE", "prompt": "task-zero"}
            actions.append((name, arguments))
            receipt = await app.submit_thread(thread_id=parent.thread_id, prompt="Parent request")
            result = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
            assert result.status == "completed", result
            return await app._store.child_executions.get(receipts[-1]["execution_id"])

        yield app, parent, submit


@asynccontextmanager
async def transcript_client(app):
    @asynccontextmanager
    async def factory():
        yield app

    server = create_webui(factory, api_key="child-test-key")
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://localhost",
            headers={"Authorization": "Bearer child-test-key"},
        ) as client:
            yield client


async def child_transcript(client, thread_id):
    response = await client.get(f"/api/threads/{thread_id}/transcript", params={"limit": 100})
    assert response.status_code == 200, response.text
    return response.json()


async def test_running_child_checkpoint_transcript_marker_and_replacement(tmp_path, monkeypatch):
    blocked, release, marker = Event(), Event(), Event()
    checkpoints = []
    box = {}

    async def model(messages, info):
        app = box["app"]
        active = next(iter(app._subagent_operator._active.values()))
        head = await app._store.child_executions.latest_for_child(active.stream.thread_id)
        checkpoints.append(head.selected_checkpoint)
        if len(checkpoints) == 1:
            yield "PROGRESS_TEXT"
            yield {0: DeltaToolCall(name="run_code", json_args='{"code":"1 + 1"}')}
        else:
            blocked.set()
            await release.wait()
            yield "FINAL_RESULT"

    async with child_app(tmp_path, monkeypatch, model) as (app, parent, submit):
        box["app"] = app
        publish = app._live_hub.publish
        published = []

        async def observe(**kwargs):
            await publish(**kwargs)
            if kwargs["run_kind"] == "child":
                published.extend(kwargs["events"])
                if any("a13n.harness_ui.checkpoint" in event.model_dump_json() for event in kwargs["events"]):
                    marker.set()

        monkeypatch.setattr(app._live_hub, "publish", observe)
        try:
            head = await submit()
            with fail_after(10):
                await blocked.wait()
                await marker.wait()
            current = await app._store.child_executions.get(head.execution_id)
            assert len(checkpoints) == 2 and checkpoints[0] != checkpoints[1]
            assert checkpoints[0] not in await app._store.objects.references(ObjectKind.child_checkpoint)
            checkpoint = await app._store.read_child_checkpoint(current)
            assert not checkpoint.terminal and checkpoint.display.final_answer is None
            assert current.status == "running" and not current.resumable
            with pytest.raises(ThreadError, match="unavailable"):
                await app.saved_child_outputs(parent.thread_id, current.execution_id)
            info = await app._subagent_operator.inspect_executions(
                parent_thread_id=parent.thread_id, execution_id=head.execution_id
            )
            waited = await app._subagent_operator.wait_executions(
                parent_thread_id=parent.thread_id, execution_id=head.execution_id, timeout_seconds=0
            )
            assert info.executions[0].status == waited.executions[0].status == "running"
            assert not info.executions[0].resumable and not waited.executions[0].resumable
            # A running child publishes its index even before a browser opens it.
            digest = current.selected_checkpoint.logical_digest
            with fail_after(10):
                while await app._store.inspections.header(current.child_thread_id, digest) is None:
                    await sleep(0.01)
            assert (await app._projections.detail(current.child_thread_id)).continuation_id == digest
            async with transcript_client(app) as client:
                page = await child_transcript(client, current.child_thread_id)
                assert page["continuation_id"] == current.selected_checkpoint.logical_digest
                assert "PROGRESS_TEXT" in json.dumps(page) and "FINAL_RESULT" not in json.dumps(page)
                with fail_after(10):
                    while not any(
                        current.selected_checkpoint.logical_digest in event.model_dump_json() for event in published
                    ):
                        await sleep(0)
                release.set()
                await app._subagent_operator.wait_executions(
                    parent_thread_id=parent.thread_id, execution_id=head.execution_id, timeout_seconds=10
                )
                ended = await app._store.child_executions.get(head.execution_id)
                assert ended.status == "succeeded" and ended.resumable
                page = await child_transcript(client, ended.child_thread_id)
                assert "PROGRESS_TEXT" in json.dumps(page) and "FINAL_RESULT" in json.dumps(page)
                assert page["turns"][-1]["final_position"] is not None
                assert page["continuation_id"] == ended.selected_checkpoint.logical_digest
                assert checkpoints[1] not in await app._store.objects.references(ObjectKind.child_checkpoint)
                assert (
                    "FINAL_RESULT"
                    in (await app.saved_child_outputs(parent.thread_id, head.execution_id)).model_dump_json()
                )
        finally:
            release.set()


async def test_child_resumed_twice_has_one_cumulative_transcript(tmp_path, monkeypatch):
    count = 0

    async def model(messages, info):
        nonlocal count
        yield f"answer-{count}"
        count += 1

    async with child_app(tmp_path, monkeypatch, model) as (app, parent, submit):
        head = await submit()
        child_id = head.child_thread_id
        for segment in range(3):
            if segment:
                head = await submit("resume_subagent", execution_id=head.execution_id, prompt=f"task-{segment}")
            await app._subagent_operator.wait_executions(
                parent_thread_id=parent.thread_id, execution_id=head.execution_id, timeout_seconds=10
            )
            assert head.child_thread_id == child_id and head.segment_index == segment
        latest = await app._store.child_executions.latest_for_child(child_id)
        from a13n_harness_ui.thread_projection import build_thread_inspection

        thread = await app._store.threads.get(child_id)
        checkpoint = await app._store.read_child_checkpoint(latest)
        full = build_thread_inspection(thread, checkpoint, child_succeeded=True)
        tail = build_thread_inspection(
            thread, checkpoint, child_succeeded=True, indexed_child_prefix=full.sealed_prefix_digest
        )
        assert tail.entry_offset > 0 and tail.turn_offset == 2
        assert tail.entries == full.entries[tail.entry_offset :]
        assert tail.turns == full.turns[tail.turn_offset :]
        async with transcript_client(app) as client:
            page = await child_transcript(client, child_id)
            text = json.dumps(page)
            assert all(f"answer-{segment}" in text for segment in range(3))
            assert "task-zero" in text and "task-1" in text and "task-2" in text
            assert page["continuation_id"] == latest.selected_checkpoint.logical_digest


async def test_checkpoint_cas_conflict_preserves_newer_writer(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from a13n_harness.state import encode_messages
    from pydantic_ai.messages import ModelResponse, TextPart

    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        assert calls == 1  # The conflicting second request must not reach the Model.
        yield {0: DeltaToolCall(name="run_code", json_args='{"code":"1 + 1"}')}

    async with child_app(tmp_path, monkeypatch, model) as (app, _parent, submit):
        repository = app._store.child_executions
        select = repository.select_checkpoint
        selected, conflict = [], Event()

        async def race(**kwargs):
            if selected:
                candidate = await app._store.objects.read_model(kwargs["checkpoint"], StoredChildCheckpoint)
                winner = candidate.model_copy(
                    update={
                        "created_at": datetime.now(UTC),
                        "harness_state": candidate.harness_state.model_copy(
                            update={
                                "message_history_json": encode_messages(
                                    (
                                        *candidate.harness_state.message_history,
                                        ModelResponse(parts=[TextPart("OTHER_WRITER")]),
                                    )
                                )
                            }
                        ),
                    }
                )
                envelope = await app._store.objects.publish_model(object_kind=ObjectKind.child_checkpoint, value=winner)
                selected.append(envelope.ref)
                await select(**{**kwargs, "checkpoint": envelope.ref})
                try:
                    return await select(**kwargs)
                finally:
                    conflict.set()
            selected.append(kwargs["checkpoint"])
            return await select(**kwargs)

        monkeypatch.setattr(repository, "select_checkpoint", race)
        head = await submit()
        with fail_after(10):
            await conflict.wait()
            while head.execution_id in await app._subagent_operator.active_execution_ids():
                await sleep(0)
        current = await repository.get(head.execution_id)
        assert current.selected_checkpoint == selected[-1] and not current.resumable
        assert selected[-1] in await app._store.objects.references(ObjectKind.child_checkpoint)
        async with transcript_client(app) as client:
            page = await child_transcript(client, head.child_thread_id)
            assert "OTHER_WRITER" in json.dumps(page)
            assert page["continuation_id"] == selected[-1].logical_digest


@pytest.mark.parametrize("terminal_status", ["lost", "failed", "cancelled"])
async def test_unavailable_and_recorded_terminal_child_keep_readable_progress(tmp_path, monkeypatch, terminal_status):
    from unittest.mock import AsyncMock

    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.errors import StoreIntegrityError
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    blocked, release = Event(), Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "SURVIVING_PROGRESS"
            yield {0: DeltaToolCall(name="run_code", json_args='{"code":"1 + 1"}')}
        else:
            blocked.set()
            await release.wait()
            yield "must not finish"

    async with child_app(tmp_path, monkeypatch, model) as (app, parent, submit):
        head = await submit()
        with fail_after(10):
            await blocked.wait()
        head = await app._store.child_executions.get(head.execution_id)
        # Model an unplanned exit's durable postcondition: no terminal update was
        # recorded. Teardown still closes native resources in the test process.
        monkeypatch.setattr(app._store.child_executions, "finish", AsyncMock())
        await app.cancel_child_execution(parent_thread_id=parent.thread_id, execution_id=head.execution_id)
        await app._subagent_operator.wait_executions(
            parent_thread_id=parent.thread_id, execution_id=head.execution_id, timeout_seconds=10
        )
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=tmp_path / "a13n-harness-ui.yaml") as successor:
        async with transcript_client(successor) as client:
            page = await child_transcript(client, head.child_thread_id)
            assert "SURVIVING_PROGRESS" in json.dumps(page)
            current = await successor._store.child_executions.get(head.execution_id)
            assert current.status == "running" and not current.resumable
            projected = await successor.query_child_executions(
                parent_thread_id=parent.thread_id, execution_id=head.execution_id
            )
            assert projected.executions[0].local_status == "unavailable"
            with pytest.raises(StoreIntegrityError, match="not resumable"):
                await successor._store.child_executions.resume(
                    previous_execution_id=head.execution_id,
                    execution_id="rejected",
                    child_run_id="rejected-run",
                    run_composition=head.run_composition,
                )
            if terminal_status == "lost":
                await successor._subagent_operator._lose_after_acceptance(
                    head.execution_id, expected_checkpoint=current.selected_checkpoint
                )
            else:
                await successor._store.child_executions.finish(
                    execution_id=head.execution_id,
                    status=terminal_status,
                    expected_checkpoint=current.selected_checkpoint,
                    checkpoint=None,
                )
            ended = await successor._store.child_executions.get(head.execution_id)
            assert ended.status == terminal_status and not ended.resumable
            assert ended.selected_checkpoint == current.selected_checkpoint
            assert "SURVIVING_PROGRESS" in json.dumps(await child_transcript(client, head.child_thread_id))
            with pytest.raises(StoreIntegrityError, match="not resumable"):
                await successor._store.child_executions.resume(
                    previous_execution_id=head.execution_id,
                    execution_id="rejected",
                    child_run_id="rejected-run",
                    run_composition=head.run_composition,
                )
            saved = await successor.saved_child_outputs(parent.thread_id, head.execution_id)
            assert "SURVIVING_PROGRESS" in saved.model_dump_json()
