from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness import HarnessState
from a13n_service.agents.domain import (
    AgentConfig,
    EffectiveAgentConfig,
    EffectiveAgentModel,
    canonical_digest,
)
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.database import DatabaseMigrator
from a13n_service.database.metadata import service_metadata
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.interactions.state import HostContinuationState, RunStateEnvelope
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.storage import transaction
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

RUN_ID = "run_1234567890abcdef"
AGENT_ID = "agt_1234567890abcdef"
AGENT_REVISION_ID = "agtr_1234567890abcdef"
MODEL_ID = "mdl_1234567890abcdef"
MODEL_KEY = "primary"
ATTEMPT_ID = "rat_1234567890abcdef"
TENANT_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
SESSION_ID = "sess_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
NOW = datetime(2026, 9, 3, 0, 30, tzinfo=UTC)


def agent_config() -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "model": {
                "model_key": MODEL_KEY,
                "settings": {"temperature": 0.2},
                "characteristics": {"context_window": 128000},
            },
            "instructions": "Be helpful.",
            "input_adapter": {"adapter_key": "native", "config": {}},
            "protocol": {
                "schema_version": "1",
                "public_name": "Support",
                "output_modes": ["text"],
                "limits": {},
            },
        }
    )


def effective_agent_config() -> EffectiveAgentConfig:
    base = agent_config()
    execution = ModelExecutionSnapshot(
        model_id=MODEL_ID,
        model_key=MODEL_KEY,
        upstream_model="gpt-5.6-terra",
        model_api="openai.responses",
    )
    candidate = EffectiveAgentConfig(
        resolved_model=EffectiveAgentModel(
            execution=execution,
            settings=base.model.settings,
            characteristics=base.model.characteristics,
        ),
        runtime_lock_digest="a" * 64,
        instructions=base.instructions,
        input_adapter=base.input_adapter,
        client_tools=base.client_tools,
        output_spec=base.output_spec,
        retries=base.retries,
        secret_requirements=base.secret_requirements,
        asset_publication=base.asset_publication,
        protocol=base.protocol,
        content_digest="0" * 64,
    )
    payload = candidate.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    return candidate.model_copy(update={"content_digest": canonical_digest(payload)})


def initial_state() -> RunStateEnvelope:
    harness = HarnessState.new(thread_id=THREAD_ID)
    return RunStateEnvelope(
        run_id=RUN_ID,
        thread_id=harness.thread_id,
        checkpoint_seq=0,
        checkpoint_kind="initial",
        input_disposition="pending",
        last_checkpoint_run_attempt_id=None,
        last_checkpoint_fence=0,
        writer_fence=0,
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
        runtime_lock_digest="a" * 64,
        harness_schema_version="1",
        harness=harness,
        host=HostContinuationState(),
        outcome_candidate=None,
    )


def progress_state(
    previous: RunStateEnvelope,
    *,
    fence: int = 1,
    run_attempt_id: str = ATTEMPT_ID,
) -> RunStateEnvelope:
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=previous.checkpoint_seq + 1,
        checkpoint_kind="progress",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=run_attempt_id,
        last_checkpoint_fence=fence,
        writer_fence=fence,
        outcome_candidate=None,
    )
    return RunStateEnvelope.model_validate(payload)


@pytest.fixture
async def interaction_object_store(tmp_path: Path):
    return await LocalObjectStore.create(tmp_path / "interaction-objects")


@pytest.fixture
async def interaction_sessions(
    service_sqlite_database: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=service_sqlite_database))
    sessions = create_session_factory(engine)
    await _seed_interaction_database(sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
async def migrated_interaction_sessions(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], SQLiteConfig | PostgreSQLConfig]]:
    config = (
        SQLiteConfig(path=tmp_path / "migrated-interactions.sqlite3")
        if request.param == "sqlite"
        else PostgreSQLConfig(url=request.getfixturevalue("pg_url"))
    )
    await asyncio.to_thread(DatabaseMigrator(config).upgrade)
    engine = create_sql_engine(config)
    sessions = create_session_factory(engine)
    try:
        await _seed_interaction_database(sessions)
        yield sessions, config
    finally:
        if isinstance(config, PostgreSQLConfig):
            async with transaction(sessions) as database:
                await database.execute(text("TRUNCATE TABLE run_attempts CASCADE"))
        await engine.dispose()
        if isinstance(config, PostgreSQLConfig):
            await asyncio.to_thread(DatabaseMigrator(config).downgrade, "base")


@pytest.fixture
async def postgres_interaction_sessions(
    pg_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(PostgreSQLConfig(url=pg_url))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    await _seed_interaction_database(sessions)
    try:
        yield sessions
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(service_metadata().drop_all)
        await engine.dispose()


async def _seed_interaction_database(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(sessions) as database:
        database.add(OrganizationRecord(id=TENANT_ID, name="Test", created_at=NOW, updated_at=NOW))
        database.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=TENANT_ID,
                name="Test",
                normalized_name="test",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await database.flush()
        database.add(
            AgentRecord(
                id=AGENT_ID,
                organization_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                source="custom",
                name="Test Agent",
                normalized_name="test agent",
                description=None,
                version=1,
                current_revision_id=AGENT_REVISION_ID,
                enabled=True,
                archived_at=None,
                duplicated_from_agent_id=None,
                duplicated_from_revision_id=None,
                created_by_type="user",
                created_by_id=USER_ID,
                updated_by_type="user",
                updated_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        database.add(
            AgentRevisionRecord(
                id=AGENT_REVISION_ID,
                organization_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                agent_id=AGENT_ID,
                version=1,
                plugin_runtime_mode="on_demand",
                config=agent_config().model_dump(mode="json", by_alias=True),
                config_digest="b" * 64,
                resolved_model={},
                resolved_plugin_versions=[],
                runtime_lock_digest="a" * 64,
                resolved_skills=[],
                connector_tools=[],
                mcp_tools=[],
                resolved_subagents=[],
                content_digest="c" * 64,
                source_revision_id=None,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )
