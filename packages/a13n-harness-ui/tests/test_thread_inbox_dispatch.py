import pytest
from a13n_harness import envelope_ids, input_source
from a13n_harness.content import request_input_content
from a13n_harness.envelopes import Envelope, ExecutionRef, Sender
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage.continuations import read_continuation
from anyio import Event, fail_after, sleep
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings, _write_configuration
from .test_bot import ready

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def message(identity, *, delivery="steer", kind="agent_message"):
    return Envelope(
        id=identity,
        kind=kind,
        sender=Sender(kind="agent", thread_id="thread-source", name="Source"),
        text=f"Verified fact {identity}",
        delivery=delivery,
    )


async def drained(app, thread_id):
    with fail_after(10):
        while await app._store.thread_inbox.pending(thread_id) or await app._root_runs.active(thread_id):
            await sleep(0.01)


async def test_idle_burst_starts_one_run_and_consumes_only_selected_checkpoint(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    seen = []

    async def model(messages, info):
        seen.append([part for msg in messages for part in request_input_content(msg) if input_source(part.metadata)])
        yield "Integrated"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread()
        # Retain the burst before scheduling, as transactional producers do.
        for identity in ("one", "two", "three"):
            await app._store.thread_inbox.put(thread.thread_id, message(identity))
        app._inbox.schedule(thread.thread_id)
        await drained(app, thread.thread_id)
        assert len(seen) == 1 and len(seen[0]) == 3
        head = await app._store.threads.get(thread.thread_id)
        saved = await read_continuation(app._store.objects, head.continuation)
        assert set(envelope_ids(saved.harness_state)) == {"one", "two", "three"}
        app._inbox.schedule(thread.thread_id)
        await sleep(0.05)
        assert len(seen) == 1


async def test_active_next_run_waits_for_settlement_and_offered_inputs_are_not_repeated(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    entered, release = Event(), Event()
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            entered.set()
            await release.wait()
        yield "Done"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Initial")
        with fail_after(5):
            await entered.wait()
        await app._inbox.deliver(thread.thread_id, message("later", delivery="next_run"))
        await sleep(0.05)
        assert requests == 1 and len(await app._store.thread_inbox.pending(thread.thread_id)) == 1
        release.set()
        await app.wait_root_operation(receipt.receipt_id)
        await drained(app, thread.thread_id)
        assert requests == 2


async def test_restart_recovers_pending_input_without_replaying_selected_input(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "data")
    calls = []

    async def model(messages, info):
        calls.append(True)
        yield "Saved"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        await app._store.thread_inbox.put(thread.thread_id, message("restart"))
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        await drained(app, thread.thread_id)
        assert len(calls) == 1
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        await sleep(0.05)
        assert not await app._store.thread_inbox.pending(thread.thread_id)
        assert len(calls) == 1


async def test_bot_own_result_starts_idle_bot_and_retains_full_inline_reply(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  bot: {enabled: true}\n")
    seen = []

    async def model(messages, info):
        seen.extend(
            part.value for msg in messages for part in request_input_content(msg) if input_source(part.metadata)
        )
        yield "Integrated result"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        await ready(app)
        body = "complete final reply " * 4000
        await app._inbox.deliver(
            BOT_THREAD_ID,
            Envelope(
                id="result-child",
                kind="result",
                sender=Sender(kind="agent", thread_id="thread-child"),
                execution=ExecutionRef(execution_id="execution-child", kind="agent", name="worker", state="succeeded"),
                text=body,
            ),
        )
        await drained(app, BOT_THREAD_ID)
        assert len(seen) == 1 and body in seen[0]
        assert (await app._store.bot.inputs())[0].source == "automatic"


async def test_archive_withdraws_inputs_and_does_not_start_run(tmp_path):
    from a13n_harness_ui.surfaces import ThreadMetadataMutation, ThreadMetadataPatch

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread()
        await app._store.thread_inbox.put(thread.thread_id, message("discard"))
        await app.update_thread_metadata(
            thread_id=thread.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=thread.metadata_version, patch=ThreadMetadataPatch(archived=True)
            ),
        )
        assert not await app._store.thread_inbox.pending(thread.thread_id)
        assert not await app._root_runs.active(thread.thread_id)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("error", [None, "Saved provider failure"])
async def test_bot_failure_note_is_hidden_dynamic_context_in_both_delivery_paths(tmp_path, monkeypatch, active, error):
    from a13n_harness_ui.bot_models import BotSubmit

    from .test_bot import delivered

    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  bot: {enabled: true}\n")
    entered, release = Event(), Event()
    seen = []

    async def model(messages, info):
        seen.append(tuple(part for msg in messages for part in request_input_content(msg)))
        if active and len(seen) == 1:
            entered.set()
            await release.wait()
        yield "Integrated"

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await app._threads.bot_thread()
        if active:
            await bot.submit(BotSubmit(input_id="human", text="Working"))
            with fail_after(5):
                await entered.wait()
        state = await app._store.bot.state()
        await app._store.bot.save_state(state.model_copy(update={"activated": True, "last_error": error}))
        await app._inbox.deliver(BOT_THREAD_ID, message("failure-context"))
        await delivered(bot)
        release.set()
        await drained(app, BOT_THREAD_ID)
        notes = [
            part
            for part in seen[-1]
            if isinstance(part.value, str) and part.value.startswith("A previous Bot input failed.")
        ]
        assert len(notes) == (1 if error else 0)
        if error:
            assert error in notes[0].value and not notes[0].metadata.display
            assert "fresh opportunity, not a replay" in notes[0].value
        assert not await app._store.thread_inbox.pending(BOT_THREAD_ID)
