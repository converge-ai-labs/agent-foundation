from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInboxInput, BotSubmit
from a13n_harness_ui.connector_models import ConnectorConfiguration, ConnectorInput
from a13n_harness_ui.storage.connectors import enqueue_inputs
from a13n_harness_ui.storage.database import transaction
from a13n_harness_ui.storage.models import ConnectorConfigurationRecord
from a13n_harness_ui.surfaces import RootControlResult
from anyio import Event, fail_after

from .test_app import (
    _CompletedReconstructor,
    _DeferredReconstructor,
    _settings,
    _SlowReconstructor,
)
from .test_bot import _write_configuration, consumed, ready

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def event(source_id: str, *, event_type: str = "changed", connector_id: str = "connector-example") -> BotInboxInput:
    return BotInboxInput(
        input_id=f"input-{source_id}",
        source="connector",
        created_at=datetime.now(UTC),
        connector_id=connector_id,
        event_type=event_type,
        source_event_id=source_id,
        text=f"Event {source_id}",
    )


async def retain(app, connector_id, inputs):
    await app._store.connectors.retain(
        connector_id,
        tuple(
            ConnectorInput.model_validate({k: v for k, v in item.model_dump().items() if k != "source"})
            for item in inputs
        ),
    )


async def configure(app) -> ConnectorConfiguration:
    configuration = ConnectorConfiguration(
        connector_id="connector-example",
        kind="example",
        deliver_to_bot=True,
        selected_events=("changed",),
        credentials={"api_key": "private-test-key"},
        settings={"scope": "private-test-scope"},
    )
    await app._store.connectors.configure(configuration)
    return configuration


async def test_connector_configuration_selection_dedup_exact_batch_and_restart(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        configuration = await configure(app)
        assert "private-test" not in configuration.model_dump_json()
        assert "private-test" not in repr(configuration)
        a, b, ignored = event("a"), event("b"), event("ignored", event_type="unselected")
        await retain(app, configuration.connector_id, (a, a.model_copy(update={"input_id": "duplicate"}), ignored))
        batch = await app._store.bot_inbox.pending()
        assert batch == (a,)
        await retain(app, configuration.connector_id, (b,))
        await app._store.bot_inbox.delivered(batch)
        assert await app._store.bot_inbox.pending() == (b,)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        assert (
            await app._store.connectors.configuration(configuration.connector_id)
        ).credentials == configuration.credentials
        assert await app._store.bot_inbox.pending() == (b,)
        await app._store.connectors.configure(configuration.model_copy(update={"selected_events": ()}))
        assert not await app._store.bot_inbox.pending()


async def test_connector_cursor_and_inbox_share_a_short_transaction(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        configuration = await configure(app)
        # A provider uses its own fixed business-state row here; settings stands in for a cursor.
        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(app._store.database.sessions) as session:
                row = await session.get(ConnectorConfigurationRecord, configuration.connector_id)
                row.settings = '{"cursor":"advanced"}'
                await enqueue_inputs(
                    session,
                    configuration.connector_id,
                    (
                        ConnectorInput.model_validate(
                            {k: v for k, v in event("atomic").model_dump().items() if k != "source"}
                        ),
                    ),
                )
                raise RuntimeError("rollback")
        assert not await app._store.bot_inbox.pending()
        assert (
            await app._store.connectors.configuration(configuration.connector_id)
        ).settings == configuration.settings
        async with transaction(app._store.database.sessions) as session:
            row = await session.get(ConnectorConfigurationRecord, configuration.connector_id)
            row.settings = '{"cursor":"advanced"}'
            await enqueue_inputs(
                session,
                configuration.connector_id,
                (
                    ConnectorInput.model_validate(
                        {k: v for k, v in event("atomic").model_dump().items() if k != "source"}
                    ),
                ),
            )
        assert len(await app._store.bot_inbox.pending()) == 1
        assert (await app._store.connectors.configuration(configuration.connector_id)).settings == {
            "cursor": "advanced"
        }


async def test_selected_connector_batch_activates_idle_bot_and_publishes_final_text(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        app._root_runs._executor._agents = _CompletedReconstructor()
        await configure(app)
        await retain(app, "connector-example", (event("start"),))
        await bot.deliver_automatic_inputs()
        snapshot = await bot.snapshot()
        assert snapshot.state.activated
        assert not await app._store.bot_inbox.pending()
        item = snapshot.inputs[0]
        assert (await consumed(app, item.input_id)).state == "completed"
        snapshot = await bot.snapshot()
        assert [message.text for message in snapshot.messages] == ["root complete"]
        assert snapshot.thread.completion is not None
        assert "private-test" not in (await bot.snapshot()).model_dump_json()
        count = len((await bot.snapshot()).inputs)
        await bot.deliver_automatic_inputs()
        assert len((await bot.snapshot()).inputs) == count


async def test_busy_connector_rejection_waits_for_next_period_and_acceptance_deletes_only_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        await bot.submit(BotSubmit(input_id="busy", text="Long running work"))
        with fail_after(10):
            await started.wait()
        await configure(app)
        a, b = event("a"), event("arrived-during-steer")
        await retain(app, "connector-example", (a,))
        active = await app._root_runs.active(BOT_THREAD_ID)
        attempts = []

        async def rejected(**kwargs):
            attempts.append(kwargs)
            return RootControlResult(receipt_id=active.receipt.receipt_id, accepted=False)

        monkeypatch.setattr(app._root_runs, "steer", rejected)
        await bot.deliver_automatic_inputs()
        assert len(attempts) == 1 and attempts[0]["receipt_id"] == active.receipt.receipt_id
        assert await app._store.bot_inbox.pending() == (a,)
        assert len((await bot.snapshot()).inputs) == 1  # No replacement Run after rejection.
        native_steer = type(app._root_runs).steer

        async def accepted(**kwargs):
            await retain(app, "connector-example", (b,))
            return await native_steer(app._root_runs, **kwargs)

        monkeypatch.setattr(app._root_runs, "steer", accepted)
        await bot.deliver_automatic_inputs()
        assert await app._store.bot_inbox.pending() == (b,)
        await app._root_runs.cancel(receipt_id=active.receipt.receipt_id)
        await consumed(app, "busy")


async def test_connector_pause_and_pending_decision_keep_inbox_without_waking(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        app._root_runs._executor._agents = _DeferredReconstructor()
        await configure(app)
        a = event("waiting")
        await retain(app, "connector-example", (a,))
        await bot.pause(True)
        await bot.deliver_automatic_inputs()
        assert not (await bot.snapshot()).inputs
        await bot.submit(BotSubmit(input_id="decision", text="Ask first"))
        await consumed(app, "decision")
        await bot.pause(False)
        await bot.deliver_automatic_inputs()
        assert (await bot.snapshot()).pending_decision
        assert len((await bot.snapshot()).inputs) == 1
        assert await app._store.bot_inbox.pending() == (a,)
