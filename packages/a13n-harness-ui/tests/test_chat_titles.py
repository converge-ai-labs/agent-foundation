import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.chat_titles import title_context
from a13n_harness_ui.surfaces import RootOperationStatus, ThreadMetadataMutation, ThreadMetadataPatch
from anyio import Event, fail_after, sleep

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
