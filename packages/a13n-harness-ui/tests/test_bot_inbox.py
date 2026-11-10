from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotSubmit
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.storage.bot_inbox import serialize_batch
from a13n_harness_ui.surfaces import RootControlResult
from anyio import Event, fail_after

from .test_app import _CompletedReconstructor, _settings, _SlowReconstructor
from .test_app import _write_configuration as base_configuration
from .test_bot import _write_configuration, consumed, ready
from .test_connectors import configure, event, retain

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def policy(app, root, **changes):
    import yaml

    document = yaml.safe_load(root.read_text())
    document.setdefault("webui", {}).setdefault("bot", {}).update(changes)
    await app.mutate_configuration(
        relative_path=root.name, request=ResourceMutationRequest(content=yaml.safe_dump(document))
    )


async def test_default_off_enable_arms_without_run_and_first_due_tick_needs_no_chat(tmp_path: Path) -> None:
    root = base_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        app._root_runs._executor._agents = _CompletedReconstructor()
        assert not (await bot.snapshot()).enabled
        with pytest.raises(HarnessUiError) as error:
            await bot.submit(BotSubmit(input_id="off", text="No run"))
        assert error.value.code == "bot_disabled"
        await bot.tick(now=datetime.now(UTC) + timedelta(days=1))
        assert not await app._store.bot_inbox.pending()
        await policy(app, root, enabled=True)
        snapshot = await bot.snapshot()
        due = snapshot.state.next_heartbeat_at
        assert due and due > datetime.now(UTC)
        assert not snapshot.thread and not snapshot.inputs and not snapshot.state.activated
        await bot.tick(now=due)
        batch = await app._store.bot_inbox.pending()
        assert len(batch) == 1 and batch[0].source == "heartbeat"
        await bot.tick(now=due + timedelta(days=10))
        assert await app._store.bot_inbox.pending() == batch
        await bot.deliver_automatic_inputs()
        item = (await bot.snapshot()).inputs[-1]
        assert item.source == "automatic" and batch[0].input_id in item.text
        assert (await consumed(app, item.input_id)).state == "completed"
        assert not (await bot.snapshot()).messages


async def test_busy_run_retains_ticks_and_fair_mixed_batch_steers_exact_receipt(tmp_path: Path, monkeypatch) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        await bot.submit(BotSubmit(input_id="long", text="Long running work"))
        with fail_after(10):
            await started.wait()
        due = (await bot.snapshot()).state.next_heartbeat_at
        await configure(app)
        for index in range(40):
            await retain(app, "connector-example", (event(str(index)),))
        await bot.tick(now=due)
        batch = await app._store.bot_inbox.pending()
        assert len(batch) == 32 and batch[0].source == "heartbeat"
        assert len(serialize_batch(batch).encode()) <= 60 * 1024
        receipt = (await app._root_runs.active(BOT_THREAD_ID)).receipt.receipt_id
        attempts = []

        async def reject(**kwargs):
            attempts.append(kwargs)
            return RootControlResult(receipt_id=receipt, accepted=False)

        monkeypatch.setattr(app._root_runs, "steer", reject)
        await bot.deliver_automatic_inputs()
        assert attempts[0]["receipt_id"] == receipt
        assert len((await bot.snapshot()).inputs) == 1
        assert await app._store.bot_inbox.pending() == batch
        await bot.tick(now=due + timedelta(hours=3))
        assert await app._store.bot_inbox.pending() == batch  # Selected tick identity is immutable.

        async def accept(**kwargs):
            await retain(app, "connector-example", (event("new"),))
            return RootControlResult(receipt_id=receipt, accepted=True)

        monkeypatch.setattr(app._root_runs, "steer", accept)
        next_due = (await bot.snapshot()).state.next_heartbeat_at
        await bot.deliver_automatic_inputs()
        remainder = await app._store.bot_inbox.pending()
        assert len(remainder) == 10 and all(item.source == "connector" for item in remainder)
        assert (await bot.snapshot()).state.next_heartbeat_at == next_due
        await app._root_runs.cancel(receipt_id=receipt)
        await consumed(app, "long")


async def test_off_pause_missing_model_and_heartbeat_policy_preserve_selected_inputs(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await configure(app)
        await bot.pause(True)
        due = (await bot.snapshot()).state.next_heartbeat_at
        await bot.tick(now=due)
        await retain(app, "connector-example", (event("kept"),))
        batch = await app._store.bot_inbox.pending()
        assert len(batch) == 2
        await bot.deliver_automatic_inputs()
        assert not (await bot.snapshot()).inputs
        await policy(app, root, enabled=False)
        await retain(app, "connector-example", (event("while-off"),))
        await bot.tick(now=due + timedelta(days=1))
        assert len(await app._store.bot_inbox.pending()) == 3
        await policy(app, root, heartbeat_enabled=False)
        remaining = await app._store.bot_inbox.pending()
        assert len(remaining) == 2 and all(item.source == "connector" for item in remaining)
        await policy(app, root, enabled=True)
        assert not (await bot.snapshot()).inputs
        await bot.pause(False)
        # A missing effective model holds the same batch instead of attempting admission.
        import yaml

        document = yaml.safe_load(root.read_text())
        document["defaults"].pop("agent")
        await app.mutate_configuration(
            relative_path=root.name, request=ResourceMutationRequest(content=yaml.safe_dump(document))
        )
        assert not (await bot.snapshot()).available
        await bot.deliver_automatic_inputs()
        assert await app._store.bot_inbox.pending() == remaining
        document["defaults"]["agent"] = "agent-assistant"
        await app.mutate_configuration(
            relative_path=root.name, request=ResourceMutationRequest(content=yaml.safe_dump(document))
        )
        app._root_runs._executor._agents = _CompletedReconstructor()
        await bot.deliver_automatic_inputs()
        item = (await bot.snapshot()).inputs[-1]
        assert (await consumed(app, item.input_id)).state == "completed"
        assert not await app._store.bot_inbox.pending()


async def test_serialized_batch_limit_and_delivery_opt_in(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        configuration = await configure(app)
        await app._store.connectors.configure(configuration.model_copy(update={"deliver_to_bot": False}))
        await retain(app, configuration.connector_id, (event("ignored"),))
        assert not await app._store.bot_inbox.pending()
        await app._store.connectors.configure(configuration)
        for index in range(10):
            await retain(app, configuration.connector_id, (event(str(index)).model_copy(update={"text": "界" * 5000}),))
        batch = await app._store.bot_inbox.pending()
        assert 1 < len(batch) < 10
        assert len(serialize_batch(batch).encode()) <= 60 * 1024


async def test_settlement_failure_is_supervised_and_does_not_stop_bot_loops(tmp_path: Path, monkeypatch) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        app._root_runs._executor._agents = _CompletedReconstructor()
        original = app._store.bot.save_input
        failed = False

        async def fail_once(item):
            nonlocal failed
            if item.state == "completed" and not failed:
                failed = True
                raise RuntimeError("Transient settlement write failure")
            await original(item)

        monkeypatch.setattr(app._store.bot, "save_input", fail_once)
        await bot.submit(BotSubmit(input_id="fault", text="Complete this work"))
        assert (await consumed(app, "fault")).state == "interrupted"
        assert bot._owned and (await bot.snapshot()).state.paused
        await bot.submit(BotSubmit(input_id="after-fault", text="Inspect and continue explicitly"))
        assert (await consumed(app, "after-fault")).state == "completed"


async def test_oversized_serialized_envelope_rolls_back_source_and_cannot_block_later_input(tmp_path: Path) -> None:
    from a13n_harness_ui.connector_models import ConnectorInput
    from a13n_harness_ui.storage.connectors import enqueue_inputs
    from a13n_harness_ui.storage.database import transaction
    from a13n_harness_ui.storage.models import ConnectorConfigurationRecord

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        configuration = await configure(app)
        with pytest.raises(HarnessUiError) as error:
            async with transaction(app._store.database.sessions) as session:
                row = await session.get(ConnectorConfigurationRecord, configuration.connector_id)
                row.settings = '{"cursor":"advanced"}'
                await enqueue_inputs(
                    session,
                    configuration.connector_id,
                    (
                        ConnectorInput(
                            connector_id=configuration.connector_id,
                            event_type="changed",
                            source_event_id="large",
                            text="😀" * 16384,
                        ),
                    ),
                )
        assert error.value.code == "connector_input_too_large"
        assert (
            await app._store.connectors.configuration(configuration.connector_id)
        ).settings == configuration.settings
        await retain(app, configuration.connector_id, (event("after-large"),))
        assert len(await app._store.bot_inbox.pending()) == 1
