from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from a13n_harness_ui.app import AppState, open_harness_ui_app
from a13n_harness_ui.chat_titles import title_context
from a13n_harness_ui.storage.runtime import open_local_store
from a13n_harness_ui.surfaces import RootOperationStatus, ThreadMetadataMutation, ThreadMetadataPatch
from anyio import CancelScope, Event, create_task_group, fail_after, get_cancelled_exc_class, move_on_after, sleep
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from .test_app import _CompletedReconstructor, _settings, _write_configuration


def test_title_samples_both_ends_with_one_shared_budget():
    messages = ["start " + "middle " * 3000 + "end", "开始" + "词" * 3000 + "结束"]
    first, last = title_context(messages).split("\n\n")
    assert first.startswith("start ") and first.endswith(" end")
    assert len(first.split()) == 1000
    assert last.startswith("开始") and last.endswith("结束")
    assert len(last.replace(" ", "")) == 1000


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("rename", [False, True])
async def test_async_first_title_and_explicit_regeneration(tmp_path, monkeypatch, rename):
    root = _write_configuration(tmp_path)
    model_id = "model-title" if rename else "model-primary"
    if rename:
        primary = root.parent / "models/primary.yaml"
        primary.with_name("title.yaml").write_text(primary.read_text().replace("model-primary", model_id))
        root.write_text(root.read_text() + "chat_title_model: model-title\n")
    calls = []
    started, release = Event(), Event()
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:

        async def generate(thread_id, messages, source, model_id):
            calls.append((messages, model_id))
            started.set()
            await release.wait()
            return "Suggested title"

        monkeypatch.setattr(app, "_generate_chat_title", generate)
        app._root_runs._executor._agents = _CompletedReconstructor()
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="First topic")
        with fail_after(10):
            await started.wait()
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        thread = (await app.get_thread(thread_id)).thread
        if rename:
            await app.update_thread_metadata(
                thread_id=thread_id,
                mutation=ThreadMetadataMutation(
                    expected_version=thread.metadata_version, patch=ThreadMetadataPatch(title="My title")
                ),
            )
        release.set()
        if not rename:
            with fail_after(5):
                while (await app.get_thread(thread_id)).thread.title != "Suggested title":
                    await sleep(0.02)
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Second topic")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert await app.suggest_chat_title(thread_id) == "Suggested title"
        assert calls == [(["First topic"], model_id), (["First topic", "Second topic"], model_id)]
        assert (await app.get_thread(thread_id)).thread.title == ("My title" if rename else "Suggested title")


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize("cancel_lifetime", [False, True])
@pytest.mark.parametrize("stage", ["model", "publication"])
async def test_pending_automatic_title_joins_app_shutdown_after_root_completion(
    tmp_path, monkeypatch, cancel_lifetime, stage
):
    root = _write_configuration(tmp_path)
    held, release, cancelled, ready, stop, closed = (Event() for _ in range(6))
    owners, scopes = [], []
    metadata_writes = []
    cancelled_before_storage_close = []
    storage_closing = Event()

    @asynccontextmanager
    async def tracked_store(settings):
        async with open_local_store(settings) as store:
            try:
                yield store
            finally:
                storage_closing.set()

    monkeypatch.setattr("a13n_harness_ui.app.open_local_store", tracked_store)

    async def lifetime():
        with CancelScope() as scope:
            scopes.append(scope)
            async with open_harness_ui_app(
                _settings(tmp_path / "state", shutdown_timeout_seconds=0.02),
                configuration_path=root,
                host_mode="webui",
            ) as app:
                owners.append(app)

                async def wait_for_title():
                    held.set()
                    try:
                        await release.wait()
                    except get_cancelled_exc_class():
                        cancelled.set()
                        cancelled_before_storage_close.append(not storage_closing.is_set())
                        raise

                async def model(messages, info):
                    if stage == "model":
                        await wait_for_title()
                    return ModelResponse(parts=[TextPart("Generated title")])

                async def utility_model(source, model_id, thread_id):
                    return FunctionModel(model), SimpleNamespace(settings={})

                update = app.update_thread_metadata

                async def publish(**kwargs):
                    if stage == "publication":
                        await wait_for_title()
                    metadata_writes.append(kwargs)
                    return await update(**kwargs)

                monkeypatch.setattr(app, "_utility_model", utility_model)
                monkeypatch.setattr(app, "update_thread_metadata", publish)
                app._root_runs._executor._agents = _CompletedReconstructor()
                thread_id = (await app.create_thread()).thread_id
                receipt = await app.submit_thread(thread_id=thread_id, prompt="First topic")
                with fail_after(5):
                    await held.wait()
                    result = await app.wait_root_operation(receipt.receipt_id)
                assert result.status is RootOperationStatus.completed
                assert await app._root_runs.active_count() == 0
                assert (await app.get_thread(thread_id)).thread.title is None
                ready.set()
                await stop.wait()
        closed.set()

    with fail_after(15):
        async with create_task_group() as group:
            group.start_soon(lifetime)
            await ready.wait()
            if cancel_lifetime:
                scopes[0].cancel()
            else:
                stop.set()
            # Bound title cancellation, not unrelated collaborator/storage teardown.
            # Release the old implementation's shielded join if cancellation fails.
            with move_on_after(5) as shutdown_wait:
                await cancelled.wait()
            release.set()
            await closed.wait()
    assert not shutdown_wait.cancel_called
    assert cancelled.is_set()
    assert cancelled_before_storage_close == [True]
    assert metadata_writes == []
    app = owners[0]
    assert app.state is AppState.closed
    assert storage_closing.is_set()
    assert app._operations_idle.is_set()
    assert not app._operation_scopes
    assert app._root_runs._task_group is None
