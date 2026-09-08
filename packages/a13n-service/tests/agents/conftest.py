from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import (
    Agent,
    AgentConfig,
    AgentRevisionCreateResult,
)
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
DIRECT_USER_ID = "usr_direct1234567890"
MODEL_ID = "mdl_1234567890abcdef"
MODEL_KEY = "primary"
PROVIDER_ID = "mprov_1234567890abcdef"
SECRET_ID = "sec_1234567890abcdef"


def actor(user_id: str = USER_ID) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=user_id),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-agent-agent-test",
    )


def agent_config(
    *,
    instructions: str = "Be helpful.",
    plugins: list[object] | None = None,
    skills: list[object] | None = None,
    connector_tools: tuple[dict[str, object], ...] | None = None,
    mcp_tools: tuple[dict[str, object], ...] | None = None,
    subagents: dict[str, object] | None = None,
) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "model": {
                "model_key": MODEL_KEY,
                "settings": {"temperature": 0.2},
                "characteristics": {"context_window": 128000},
            },
            "instructions": instructions,
            "input_adapter": {"adapter_key": "native", "config": {}},
            "plugins": plugins or [],
            "skills": skills or [],
            "connector_tools": connector_tools or (),
            "mcp_tools": mcp_tools or (),
            "subagents": subagents or {},
            "client_tools": [],
            "output_spec": None,
            "retries": {"tools": 2, "output": 1},
            "secret_requirements": [],
            "asset_publication": None,
            "protocol": {
                "schema_version": "1",
                "public_name": "Support",
                "output_modes": ["text"],
                "client_tools": [],
                "event_visibility": [],
                "a2a_skills": [],
                "limits": {},
            },
        }
    )


async def create_current_revision(
    service: AgentManagement,
    *,
    agent_id: str,
    expected_version: int,
    key: str,
) -> tuple[AgentRevisionCreateResult, Agent]:
    """Return the current immutable Revision created atomically with the Agent."""

    del expected_version, key
    selected = await service.queries.get(actor=actor(), agent_id=agent_id)
    revision = await service.queries.get_revision(actor=actor(), revision_id=selected.current_revision_id)
    return AgentRevisionCreateResult(agent=selected, revision=revision), selected


@pytest.fixture
async def agent_sessions(
    service_sqlite_database: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=service_sqlite_database))
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            UserRecord(
                id=USER_ID,
                email="builder@example.com",
                normalized_email="builder@example.com",
                name="Builder",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_org1234567890abcd",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_ws1234567890abcde",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="builder",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                SecretRecord(
                    id=SECRET_ID,
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    owner_type="workspace",
                    owner_id=WORKSPACE_ID,
                    key="environment_key",
                    version=1,
                    ciphertext=b"encrypted",
                    nonce=b"123456789012",
                    encryption_key_id="test-key",
                    created_at=NOW,
                    value_updated_at=NOW,
                    deleted_at=None,
                ),
                ModelProviderRecord(
                    id=PROVIDER_ID,
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    type="openai",
                    name="OpenAI",
                    normalized_name="openai",
                    configuration={},
                    credential_generation=1,
                    ciphertext=b"encrypted",
                    nonce=b"123456789012",
                    encryption_key_id="test-key",
                    enabled=True,
                    created_by_type="user",
                    created_by_id=USER_ID,
                    updated_by_type="user",
                    updated_by_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                ModelRecord(
                    id=MODEL_ID,
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    key=MODEL_KEY,
                    normalized_key=MODEL_KEY,
                    provider_id=PROVIDER_ID,
                    name="Primary",
                    description=None,
                    upstream_model="gpt-5.6-terra",
                    model_api="openai.responses",
                    settings={},
                    enabled=True,
                    created_by_type="user",
                    created_by_id=USER_ID,
                    updated_by_type="user",
                    updated_by_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
async def agent_management(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AgentManagement]:
    model_selector = AcceptedModelSelector(
        agent_sessions,
        built_in_provider_registry(),
    )
    resolver = AgentResolver(
        agent_sessions,
        model_selector,
    )
    invocation_resolver = AgentInvocationResolver(
        agent_sessions,
        model_selector,
    )
    yield AgentManagement(
        agent_sessions,
        resolver,
        invocation_resolver,
        clock=lambda: NOW,
    )


@pytest.fixture
async def agent_invocation_resolver(
    agent_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AgentInvocationResolver]:
    model_selector = AcceptedModelSelector(
        agent_sessions,
        built_in_provider_registry(),
    )
    yield AgentInvocationResolver(
        agent_sessions,
        model_selector,
    )
