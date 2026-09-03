from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness import HarnessState
from a13n_service.agents.domain import (
    AgentConfig,
    EffectiveAgentConfig,
    EffectiveAgentModel,
    EnvironmentExecutionConfig,
    canonical_digest,
)
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.database.metadata import service_metadata
from a13n_service.environments.domain import (
    EnvironmentAccess,
    EnvironmentConnectionSpec,
    EnvironmentProviderLock,
    environment_logical_digest,
)
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.interactions import HostContinuationState, RunStateEnvelope
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.storage import transaction
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
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
USER_ID = "usr_1234567890abcdef"
NOW = datetime(2026, 9, 3, 0, 30, tzinfo=UTC)


def environment_execution_config() -> EnvironmentExecutionConfig:
    connection = EnvironmentConnectionSpec(
        provider_key="test.attachment",
        schema_version="1",
        parameters={"target_id": "existing-target"},
    )
    provider_lock = EnvironmentProviderLock(
        provider_key=connection.provider_key,
        distribution_name="test-environment-provider",
        distribution_version="1.0.0",
        builtin=False,
        registration_digest_sha256="e" * 64,
    )
    target_key = "existing-target"
    logical_digest = environment_logical_digest(
        connection=connection,
        provider_package_revision_id=None,
        provider_lock=provider_lock,
        credential_bindings=(),
        access=EnvironmentAccess.full,
        target_key=target_key,
    )
    return EnvironmentExecutionConfig(
        connection=connection,
        provider_lock=provider_lock,
        credential_bindings=(),
        access="full",
        target_key=target_key,
        logical_digest_sha256=logical_digest,
    )


def effective_agent_config(
    *,
    environment: EnvironmentExecutionConfig | None = None,
) -> EffectiveAgentConfig:
    base = AgentConfig.model_validate(
        {
            "model": {
                "model_key": MODEL_KEY,
                "model_api": "openai.responses",
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
        resolved_environment=environment,
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


def initial_state(*, environment: EnvironmentExecutionConfig | None = None) -> RunStateEnvelope:
    harness = HarnessState.new()
    return RunStateEnvelope(
        run_id=RUN_ID,
        thread_id=harness.thread_id,
        checkpoint_seq=0,
        checkpoint_kind="initial",
        input_disposition="pending",
        last_checkpoint_run_attempt_id=None,
        last_checkpoint_fence=0,
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(environment=environment),
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
                config={},
                config_digest="b" * 64,
                resolved_model={},
                resolved_plugin_versions=[],
                runtime_lock_digest="a" * 64,
                resolved_skills=[],
                connector_tools=[],
                mcp_tools=[],
                resolved_environment=None,
                resolved_subagents=[],
                content_digest="c" * 64,
                source_revision_id=None,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
            )
        )
