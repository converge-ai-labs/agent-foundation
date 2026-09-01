import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from a13n_harness.errors import ModelResolutionError
from a13n_service.database.metadata import service_metadata
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.model_configs.domain import (
    ModelCapabilities,
    ModelConfig,
    ModelExecutionSnapshot,
    PrincipalRef,
    WorkspaceSecretCredential,
)
from a13n_service.model_configs.endpoint_policy import EndpointPolicy
from a13n_service.model_configs.models import ModelConfigRecord
from a13n_service.model_configs.providers import built_in_provider_registry
from a13n_service.model_configs.runtime import (
    AcceptedModelSelector,
    NativeModelFactory,
    SnapshotRunModelResolver,
    _parse_google_service_account,
)
from a13n_service.model_configs.service import ModelConfigError
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

NOW = datetime(2026, 8, 30, 11, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
MODEL_ID = "mdl_1234567890abcdef"
SECRET_ID = "sec_1234567890abcdef"


class RecordingSecretResolver:
    def __init__(self, value: str = "test-api-key") -> None:
        self.value = value
        self.calls = 0

    async def resolve(self, **_: object) -> str:
        self.calls += 1
        return self.value


def openai_resource() -> ModelConfig:
    principal = PrincipalRef(principal_type="user", principal_id=USER_ID)
    return ModelConfig(
        id=MODEL_ID,
        workspace_id=WORKSPACE_ID,
        version=1,
        name="Primary",
        description=None,
        provider_type="openai",
        model_name="gpt-5.6-terra",
        base_url="https://api.openai.com/v1",
        credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
        provider_config={},
        capabilities=ModelCapabilities(input_modalities=("text", "image"), reasoning=True),
        capability_source="catalog",
        enabled=True,
        created_by=principal,
        updated_by=principal,
        created_at=NOW,
        updated_at=NOW,
    )


def snapshot_for(resource: ModelConfig) -> ModelExecutionSnapshot:
    registry = built_in_provider_registry()
    adapter_key, adapter_version = registry.execution_identity(resource.provider_type)
    return ModelExecutionSnapshot.freeze(
        resource,
        adapter_key=adapter_key,
        adapter_version=adapter_version,
    )


@pytest.mark.anyio
async def test_snapshot_resolver_uses_exact_snapshot_and_resolves_secret_freshly() -> None:
    registry = built_in_provider_registry()
    secret_resolver = RecordingSecretResolver()
    async with httpx2.AsyncClient() as client:
        resolver = SnapshotRunModelResolver(
            snapshot=snapshot_for(openai_resource()),
            principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            registry=registry,
            endpoint_policy=EndpointPolicy.from_operator_allowlist(private_domains=("api.openai.com",)),
            secret_resolver=secret_resolver,
            model_factory=NativeModelFactory(client),
        )
        first = await resolver(cast(Any, None), MODEL_ID)
        second = await resolver(cast(Any, None), MODEL_ID)

    assert isinstance(first, OpenAIResponsesModel)
    assert isinstance(second, OpenAIResponsesModel)
    assert first.model_name == "gpt-5.6-terra"
    assert secret_resolver.calls == 2


@pytest.mark.anyio
async def test_snapshot_resolver_rejects_unavailable_adapter_version() -> None:
    registry = built_in_provider_registry()
    valid = snapshot_for(openai_resource())
    cases = (valid.model_copy(update={"adapter_version": "999"}),)
    async with httpx2.AsyncClient() as client:
        for snapshot in cases:
            resolver = SnapshotRunModelResolver(
                snapshot=snapshot,
                principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                registry=registry,
                endpoint_policy=EndpointPolicy.from_operator_allowlist(private_domains=("api.openai.com",)),
                secret_resolver=RecordingSecretResolver(),
                model_factory=NativeModelFactory(client),
            )
            with pytest.raises(ModelResolutionError) as captured:
                await resolver(cast(Any, None), MODEL_ID)
            assert captured.value.code == "accepted_model_reconstruction_failed"


@pytest.mark.anyio
async def test_openai_compatible_factory_honors_accepted_protocol() -> None:
    resource = openai_resource().model_copy(
        update={
            "provider_type": "openai_compatible",
            "model_name": "custom-model",
            "base_url": "https://models.example.com/v1",
            "provider_config": {
                "base_url": "https://models.example.com/v1",
                "api_protocol": "chat_completions",
                "auth_mode": "api_key_header",
                "api_key_header_name": "X-API-Key",
            },
        }
    )
    async with httpx2.AsyncClient() as client:
        model = NativeModelFactory(client).build(snapshot_for(resource), "secret")

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "custom-model"


@pytest.fixture
async def selector_database(
    tmp_path: Path,
) -> AsyncIterator[tuple[AcceptedModelSelector, async_sessionmaker, AsyncEngine]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "selector.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    principal = PrincipalRef(principal_type="user", principal_id=USER_ID)
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
            SecretRecord(
                id=SECRET_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="api_key",
                version=1,
                ciphertext=b"encrypted",
                nonce=b"123456789012",
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
        session.add(
            ModelConfigRecord(
                id=MODEL_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                version=1,
                name="Accepted",
                normalized_name="accepted",
                description=None,
                provider_type="openai_compatible",
                model_name="accepted-model",
                base_url="https://8.8.8.8/v1",
                credential={"source": "workspace_secret", "secret_id": SECRET_ID},
                provider_config={
                    "base_url": "https://8.8.8.8/v1",
                    "api_protocol": "chat_completions",
                    "auth_mode": "bearer",
                },
                capabilities=ModelCapabilities().model_dump(mode="json"),
                capability_source="catalog",
                enabled=True,
                created_by_type=principal.principal_type.value,
                created_by_id=principal.principal_id,
                updated_by_type=principal.principal_type.value,
                updated_by_id=principal.principal_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    selector = AcceptedModelSelector(sessions, built_in_provider_registry(), EndpointPolicy())
    try:
        yield selector, sessions, engine
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_run_acceptance_freezes_current_model_in_owning_transaction(
    selector_database: tuple[AcceptedModelSelector, async_sessionmaker, AsyncEngine],
) -> None:
    selector, sessions, _ = selector_database
    principal = PrincipalRef(principal_type="user", principal_id=USER_ID)
    prepared = await selector.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        model_id=MODEL_ID,
        invoking_principal=principal,
    )
    async with transaction(sessions) as session:
        snapshot = await selector.freeze_in_transaction(session, prepared=prepared)

    assert snapshot.model_name == "accepted-model"
    assert snapshot.credential == WorkspaceSecretCredential(secret_id=SECRET_ID)
    assert "encrypted" not in snapshot.model_dump_json()


@pytest.mark.anyio
async def test_run_acceptance_retries_when_model_changes_after_endpoint_validation(
    selector_database: tuple[AcceptedModelSelector, async_sessionmaker, AsyncEngine],
) -> None:
    selector, sessions, _ = selector_database
    principal = PrincipalRef(principal_type="user", principal_id=USER_ID)
    prepared = await selector.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        model_id=MODEL_ID,
        invoking_principal=principal,
    )
    async with transaction(sessions) as session:
        record = await session.get(ModelConfigRecord, MODEL_ID)
        assert record is not None
        record.model_name = "edited-model"
        record.version += 1
        record.updated_at = datetime(2026, 8, 30, 11, 1, tzinfo=UTC)

    with pytest.raises(ModelConfigError) as captured:
        async with transaction(sessions) as session:
            await selector.freeze_in_transaction(session, prepared=prepared)

    assert captured.value.code == "model_configuration_changed"


def test_vertex_service_account_rejects_caller_controlled_token_uri() -> None:
    credential = json.dumps({"token_uri": "https://attacker.example.com/token"})

    with pytest.raises(ValueError, match="official Google OAuth token endpoint"):
        _parse_google_service_account(credential)
