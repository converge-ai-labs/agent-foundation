"""Configuration snapshot readers overlap while concurrent edits remain serialized."""

from datetime import timedelta

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.database.metadata import service_metadata
from a13n_service.models.domain import CreateModelProviderRequest, CreateModelRequest
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelService
from a13n_service.models.service_common import ModelError
from a13n_service.storage import transaction
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from anyio import Event, create_task_group, fail_after
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError

from .conftest import NOW, ORG_ID, WORKSPACE_ID, actor, protector, seed_models


@pytest.fixture
async def postgres_models(pg_url):
    engine = create_sql_engine(PostgreSQLConfig(url=pg_url))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    await seed_models(sessions)
    try:
        yield sessions
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(service_metadata().drop_all)
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["model_disabled", "model_provider_disabled", "model_configuration_changed"])
async def test_shared_snapshot_readers_block_edits_and_revalidate_after_commit(postgres_models, change):
    registry = built_in_provider_registry()
    providers = ModelProviderService(
        postgres_models, registry, EndpointPolicy(), protector(), clock=lambda: NOW, resolve_dns_on_save=False
    )
    provider = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openrouter", name="Router", credential="test"),
    )
    model = await ModelService(postgres_models, registry, clock=lambda: NOW).create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="primary",
            name="Primary",
            provider_id=provider.id,
            upstream_model="test",
            model_api="openrouter.chat_completions",
        ),
    )
    selector = AcceptedModelSelector(postgres_models, registry)
    prepared = await selector.prepare(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, model_id=model.id, settings={})
    ready = [Event(), Event()]
    release = Event()

    async def reader(index):
        async with transaction(postgres_models) as session:
            frozen = await selector.freeze_in_transaction(session, prepared=prepared)
            assert frozen.model_id == model.id
            ready[index].set()
            await release.wait()

    if change == "model_provider_disabled":
        edit = update(ModelProviderRecord).where(ModelProviderRecord.id == provider.id).values(enabled=False)
    elif change == "model_disabled":
        edit = update(ModelRecord).where(ModelRecord.id == model.id).values(enabled=False)
    else:
        edit = update(ModelRecord).where(ModelRecord.id == model.id).values(updated_at=NOW + timedelta(seconds=1))

    with fail_after(10):
        async with create_task_group() as tasks:
            tasks.start_soon(reader, 0)
            await ready[0].wait()
            tasks.start_soon(reader, 1)
            await ready[1].wait()
            with pytest.raises(OperationalError, match="lock timeout"):
                async with transaction(postgres_models) as session:
                    await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    await session.execute(edit)
            release.set()
    async with transaction(postgres_models) as session:
        await session.execute(edit)
    with pytest.raises(ModelError) as error:
        async with transaction(postgres_models) as session:
            await selector.freeze_in_transaction(session, prepared=prepared)
    assert error.value.code == change
