"""Exercise overlapping Model namespaces with real PostgreSQL row locking."""

import asyncio
from collections.abc import AsyncIterator

import anyio
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.models.domain import CreateModelProviderRequest, CreateModelRequest
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_model_provider_catalog
from a13n_service.models.service import ModelService
from a13n_service.models.service_common import ModelError
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..resource_scope_helpers import organization_admin
from .conftest import WORKSPACE_ID, actor, protector, seed_models

pytestmark = pytest.mark.anyio


@pytest.fixture
async def postgres_models(pg_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    config = PostgreSQLConfig(url=pg_url)
    migrator = DatabaseMigrator(config)
    await anyio.to_thread.run_sync(migrator.upgrade)
    engine = create_sql_engine(config)
    sessions = create_session_factory(engine)
    try:
        await seed_models(sessions)
        yield sessions
    finally:
        await engine.dispose()
        await anyio.to_thread.run_sync(lambda: migrator.downgrade("base"))


async def test_concurrent_org_and_workspace_model_creates_have_one_winner(postgres_models):
    admin = await organization_admin(postgres_models, actor())
    registry = built_in_model_provider_catalog()
    providers = ModelProviderService(
        postgres_models,
        registry,
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
        protector(),
        resolve_dns_on_save=False,
    )
    provider = await providers.create(
        actor=admin,
        workspace_id=None,
        request=CreateModelProviderRequest(type="openai", name="Shared", credential={"api_key": "sk-test"}),
    )
    models = ModelService(postgres_models, registry)
    request = CreateModelRequest(
        provider_id=provider.id, key="coding", name="Coding", upstream_model="gpt-test", model_api="openai.responses"
    )
    outcomes = await asyncio.gather(
        models.create(actor=admin, workspace_id=None, request=request),
        models.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request),
        return_exceptions=True,
    )
    conflicts = [result for result in outcomes if isinstance(result, ModelError)]
    assert len(conflicts) == 1 and conflicts[0].code == "model_key_conflict"
    assert sum(not isinstance(result, BaseException) for result in outcomes) == 1
    assert len((await models.list(actor=actor(), workspace_id=WORKSPACE_ID)).items) == 1
