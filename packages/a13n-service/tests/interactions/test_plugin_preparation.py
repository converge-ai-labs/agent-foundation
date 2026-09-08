"""Preparation commits configuration, never Agent progress or stale-writer authority."""

import pytest
from a13n_service.agents.domain import PluginSelection, PreparedAgentPlugins
from a13n_service.digests import digest_request
from a13n_service.interactions.objects import RunStateStore, StaleStateWriter
from a13n_service.storage import ObjectStoreUnavailable
from anyio import Event, create_task_group

from .conftest import ORGANIZATION_ID, initial_state, progress_state

pytestmark = pytest.mark.anyio


def configured_initial_state():
    initial = initial_state()
    config = initial.effective_agent_config.model_copy(
        update={"plugins": (PluginSelection(instance_name="audit", plugin_key="test.audit", config={}),)}
    )
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )
    return initial.model_copy(update={"effective_agent_config": config})


def normalized_plugins(limit=5):
    return PreparedAgentPlugins(
        plugins=(
            PluginSelection(instance_name="audit", plugin_key="test.audit", config={"label": "audit", "limit": limit}),
        )
    )


async def test_preparation_is_atomic_and_does_not_apply_input(object_store):
    states = RunStateStore(object_store)
    initial = configured_initial_state()
    accepted = await states.create(ORGANIZATION_ID, initial)
    claimed = await states.claim_writer(accepted, attempt_number=1)
    prepared = await states.prepare_plugins(claimed, normalized_plugins(), attempt_number=1)
    restored = await states.read(ORGANIZATION_ID, initial.run_id)

    assert restored == prepared
    assert restored.info.version != claimed.info.version
    assert restored.writer_fence == 1
    assert restored.envelope.model_copy(update={"prepared_plugins": None}) == initial
    assert restored.envelope.checkpoint_seq == 0
    assert not restored.envelope.initial_input_applied
    assert restored.envelope.prepared_plugins == normalized_plugins()
    with pytest.raises(ValueError, match="already been prepared"):
        await states.prepare_plugins(restored, normalized_plugins(60), attempt_number=1)
    checkpoint = await states.replace(
        restored, progress_state(restored.envelope), run_attempt_id="rat_1234567890abcdef", attempt_number=1
    )
    assert checkpoint.envelope.initial_input_applied
    assert checkpoint.envelope.prepared_plugins == normalized_plugins()


@pytest.mark.parametrize("persisted", [False, True])
async def test_preparation_reconciles_lost_write_response(interaction_object_store, monkeypatch, persisted):
    states = RunStateStore(interaction_object_store)
    accepted = await states.create(ORGANIZATION_ID, configured_initial_state())
    claimed = await states.claim_writer(accepted, attempt_number=1)
    put = interaction_object_store.put

    async def lose_response(*args, **kwargs):
        if persisted:
            await put(*args, **kwargs)
        raise ObjectStoreUnavailable("response lost")

    monkeypatch.setattr(interaction_object_store, "put", lose_response)
    if persisted:
        prepared = await states.prepare_plugins(claimed, normalized_plugins(), attempt_number=1)
        assert prepared.envelope.prepared_plugins == normalized_plugins()
    else:
        with pytest.raises(ObjectStoreUnavailable):
            await states.prepare_plugins(claimed, normalized_plugins(), attempt_number=1)
        current = await states.read(ORGANIZATION_ID, claimed.envelope.run_id)
        assert current.envelope.prepared_plugins is None
        assert current.info.version == claimed.info.version


async def test_new_writer_recovers_preparation_when_response_and_reconciliation_are_lost(
    interaction_object_store, monkeypatch
):
    states = RunStateStore(interaction_object_store)
    accepted = await states.create(ORGANIZATION_ID, configured_initial_state())
    claimed = await states.claim_writer(accepted, attempt_number=1)
    put = interaction_object_store.put

    async def lose_response(*args, **kwargs):
        await put(*args, **kwargs)
        raise ObjectStoreUnavailable("response lost")

    async def unavailable(*args, **kwargs):
        raise ObjectStoreUnavailable("read unavailable")

    with monkeypatch.context() as failure:
        failure.setattr(interaction_object_store, "put", lose_response)
        failure.setattr(interaction_object_store, "stat", unavailable)
        with pytest.raises(ObjectStoreUnavailable):
            await states.prepare_plugins(claimed, normalized_plugins(), attempt_number=1)
    recovered = await states.claim_writer(await states.read(ORGANIZATION_ID, claimed.envelope.run_id), attempt_number=2)
    assert recovered.envelope.prepared_plugins == normalized_plugins()
    assert not recovered.envelope.initial_input_applied


async def test_takeover_rejects_inflight_preparation_from_old_worker(interaction_object_store, monkeypatch):
    states = RunStateStore(interaction_object_store)
    accepted = await states.create(ORGANIZATION_ID, configured_initial_state())
    old = await states.claim_writer(accepted, attempt_number=1)
    pending = Event()
    release = Event()
    put = interaction_object_store.put

    async def delay_old_preparation(*args, **kwargs):
        if kwargs["metadata"]["writer-fence"] == "1":
            pending.set()
            await release.wait()
        return await put(*args, **kwargs)

    monkeypatch.setattr(interaction_object_store, "put", delay_old_preparation)

    async def prepare_old():
        with pytest.raises(StaleStateWriter):
            await states.prepare_plugins(old, normalized_plugins(), attempt_number=1)

    async with create_task_group() as tasks:
        tasks.start_soon(prepare_old)
        await pending.wait()
        new = await states.claim_writer(await states.read(ORGANIZATION_ID, old.envelope.run_id), attempt_number=2)
        await states.prepare_plugins(new, normalized_plugins(60), attempt_number=2)
        release.set()
    restored = await states.read(ORGANIZATION_ID, old.envelope.run_id)
    assert restored.envelope.prepared_plugins == normalized_plugins(60)
    assert restored.writer_fence == 2


async def test_preparation_requires_claim_and_rejects_changes_after_execution(interaction_object_store):
    states = RunStateStore(interaction_object_store)
    accepted = await states.create(ORGANIZATION_ID, configured_initial_state())
    with pytest.raises(StaleStateWriter, match="claimed state writer"):
        await states.prepare_plugins(accepted, normalized_plugins(), attempt_number=1)
    claimed = await states.claim_writer(accepted, attempt_number=1)
    prepared = await states.prepare_plugins(claimed, normalized_plugins(True), attempt_number=1)
    changed = progress_state(prepared.envelope).model_copy(update={"prepared_plugins": normalized_plugins(1)})
    with pytest.raises(ValueError, match="prepared_plugins"):
        await states.replace(prepared, changed, run_attempt_id="rat_1234567890abcdef", attempt_number=1)
    cleared = progress_state(prepared.envelope).model_copy(update={"prepared_plugins": None})
    with pytest.raises(ValueError, match="prepared_plugins"):
        await states.replace(prepared, cleared, run_attempt_id="rat_1234567890abcdef", attempt_number=1)
