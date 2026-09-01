from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_environment_provider import build_environment_provider_catalog
from a13n_service.agent_presets.domain import AgentPresetConfig, PluginRuntimeMode
from a13n_service.agent_presets.environment_resolution import AgentEnvironmentSelectionResolver
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.resolution import AgentPresetResolver
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.database.metadata import service_metadata
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.model_configs.domain import ModelCapabilities, WorkspaceSecretCredential
from a13n_service.model_configs.endpoint_policy import EndpointPolicy
from a13n_service.model_configs.models import ModelConfigRecord
from a13n_service.model_configs.providers import built_in_provider_registry
from a13n_service.model_configs.runtime import AcceptedModelSelector
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
SECRET_ID = "sec_1234567890abcdef"


def actor(user_id: str = USER_ID) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=user_id),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-agent-preset-test",
    )


def preset_config(
    *,
    instructions: str = "Be helpful.",
    plugins: list[object] | None = None,
    connectors: dict[str, object] | None = None,
    subagents: dict[str, object] | None = None,
    environment: dict[str, object] | None = None,
) -> AgentPresetConfig:
    return AgentPresetConfig.model_validate(
        {
            "model": {
                "model_config_id": MODEL_ID,
                "settings": {"temperature": 0.2},
                "characteristics": {"context_window": 128000},
            },
            "instructions": instructions,
            "input_adapter": {"adapter_key": "native", "config": {}},
            "plugins": plugins or [],
            "skills": [],
            "connectors": connectors or {},
            "environment": environment,
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


@pytest.fixture
async def agent_preset_sessions(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "agent-presets.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", version=1, created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                version=1,
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
                version=1,
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
                    key="openai_api_key",
                    version=1,
                    ciphertext=b"encrypted",
                    nonce=b"123456789012",
                    encryption_key_id="test-key",
                    created_at=NOW,
                    value_updated_at=NOW,
                    deleted_at=None,
                ),
                ModelConfigRecord(
                    id=MODEL_ID,
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    version=1,
                    name="Primary",
                    normalized_name="primary",
                    description=None,
                    provider_type="openai",
                    model_name="gpt-5.6-terra",
                    base_url=None,
                    credential=WorkspaceSecretCredential(secret_id=SECRET_ID).model_dump(mode="json"),
                    provider_config={},
                    capabilities=ModelCapabilities(
                        input_modalities=("text",),
                        tool_calling=True,
                        structured_output=True,
                    ).model_dump(mode="json"),
                    capability_source="catalog",
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
async def agent_preset_service(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AgentPresetService]:
    model_selector = AcceptedModelSelector(
        agent_preset_sessions,
        built_in_provider_registry(),
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
    )
    environment_resolver = AgentEnvironmentSelectionResolver(
        agent_preset_sessions,
        _environment_catalog(),
    )
    resolver = AgentPresetResolver(
        agent_preset_sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.on_demand,
        environment_resolver=environment_resolver,
    )
    invocation_resolver = AgentPresetInvocationResolver(
        agent_preset_sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.on_demand,
        environment_resolver=environment_resolver,
    )
    yield AgentPresetService(
        agent_preset_sessions,
        resolver,
        invocation_resolver,
        clock=lambda: NOW,
    )


@pytest.fixture
async def agent_preset_invocation_resolver(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AgentPresetInvocationResolver]:
    model_selector = AcceptedModelSelector(
        agent_preset_sessions,
        built_in_provider_registry(),
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
    )
    environment_resolver = AgentEnvironmentSelectionResolver(
        agent_preset_sessions,
        _environment_catalog(),
    )
    yield AgentPresetInvocationResolver(
        agent_preset_sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.on_demand,
        environment_resolver=environment_resolver,
    )


@pytest.fixture
def agent_environment_service(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> EnvironmentManagementService:
    return EnvironmentManagementService(
        agent_preset_sessions,
        _environment_catalog(),
        clock=lambda: NOW,
    )


def _environment_catalog() -> FoundationEnvironmentProviderCatalog:
    return FoundationEnvironmentProviderCatalog(build_environment_provider_catalog(builtin_keys=("a13n.direct-local",)))
