"""Real PostgreSQL locks serialize provisioning and reviewed application."""

import asyncio

import anyio
import pytest
from a13n_service.agent_configuration.definition import load_definition
from a13n_service.agent_configuration.readiness import ConfigurationReadiness
from a13n_service.agent_configuration.system_agent import SystemConfigurationAgent
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.database import DatabaseMigrator
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.etags import resource_etag
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.storage import short_session, transaction
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import select, text

from ..agents.conftest import MODEL_ID, PROVIDER_ID, actor
from .test_drafts import apply_request, new_draft, save, services

pytestmark = pytest.mark.anyio


@pytest.fixture
async def postgres_configuration(agent_sessions, pg_url):
    config = PostgreSQLConfig(url=pg_url)
    migrator = DatabaseMigrator(config)
    await anyio.to_thread.run_sync(migrator.upgrade)
    engine = create_sql_engine(config)
    sessions = create_session_factory(engine)
    try:
        for record in (
            OrganizationRecord,
            UserRecord,
            WorkspaceRecord,
            RoleBindingRecord,
            ModelProviderRecord,
            ModelRecord,
        ):
            async with short_session(agent_sessions) as source:
                rows = [dict(row) for row in (await source.execute(select(record.__table__))).mappings()]
            async with transaction(sessions) as target:
                await target.execute(record.__table__.insert(), rows)
        async with transaction(sessions) as session:
            (await session.get(ModelProviderRecord, PROVIDER_ID)).credential_configured = True
            model = await session.get(ModelRecord, MODEL_ID)
            model.declarations = {**model.declarations, "supports_tools": True}
        yield sessions
    finally:
        # These rows belong exclusively to this fixture's disposable database.
        # Downgrade correctly refuses to erase populated configuration protection.
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE organizations, users, idempotency_evidence, outbox_records CASCADE")
            )
        await engine.dispose()
        await anyio.to_thread.run_sync(lambda: migrator.downgrade("base"))


async def test_concurrent_provisioning_and_apply_have_one_committed_result(postgres_configuration, pg_url):
    sessions = postgres_configuration
    conversations, drafts, applications = services(sessions)
    draft = await new_draft(conversations)
    definition = load_definition()
    ready = await ConfigurationReadiness(sessions, built_in_provider_registry(), definition).read(actor=actor())
    system = SystemConfigurationAgent(sessions, definition)
    initialized = await asyncio.gather(*[system.ensure(actor=actor(), session_id=draft.session_id) for _ in range(5)])
    assert ready.ready
    assert len({item.id for item in initialized}) == 1
    assert all(item.current_revision_id is None for item in initialized)
    saved = await save(drafts, draft)
    receipts = await asyncio.gather(
        *[
            applications.apply(
                actor=actor(),
                draft_id=saved.id,
                request=apply_request(saved),
                idempotency_key="concurrent-apply",
                if_match=resource_etag(saved.id, saved.updated_at),
            )
            for _ in range(5)
        ]
    )
    assert all(receipt == receipts[0] for receipt in receipts)
    async with short_session(sessions) as session:
        agents = tuple(await session.scalars(select(AgentRecord)))
        revisions = tuple(await session.scalars(select(AgentRevisionRecord)))
        publications = tuple(await session.scalars(select(OutboxRecord)))
    assert len(agents) == 2 and len(revisions) == 1
    assert len(publications) == 1
    assert sum(agent.system_purpose is not None for agent in agents) == 1
    migrator = DatabaseMigrator(PostgreSQLConfig(url=pg_url))
    with pytest.raises(RuntimeError, match="protected configuration assistant data"):
        await anyio.to_thread.run_sync(lambda: migrator.downgrade("838688629ca8"))
    retained = await drafts.get(actor=actor(), draft_id=saved.id)
    assert retained.status == "open" and retained.version == saved.version + 1
    assert (await applications.list_applications(actor=actor(), draft_id=draft.id, limit=10, cursor=None)).items == (
        receipts[0],
    )


async def test_shared_draft_concurrent_edits_require_current_version(postgres_configuration):
    from a13n_service.application_errors import ApplicationError

    conversations, drafts, _ = services(postgres_configuration)
    draft = await new_draft(conversations)
    outcomes = await asyncio.gather(
        save(drafts, draft, text="First editor", key="first-editor"),
        save(drafts, draft, text="Second editor", key="second-editor"),
        return_exceptions=True,
    )
    winners = [item for item in outcomes if not isinstance(item, Exception)]
    rejected = [item for item in outcomes if isinstance(item, Exception)]
    assert len(winners) == len(rejected) == 1
    assert isinstance(rejected[0], ApplicationError)
    assert await drafts.get(actor=actor(), draft_id=draft.id) == winners[0]
    assert winners[0].version == draft.version + 1
