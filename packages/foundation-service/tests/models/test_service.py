from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import (
    OrganizationRecord,
    RoleBindingRecord,
    SecurityAuditRecord,
    UserRecord,
    WorkspaceRecord,
)
from a13n_service.models.domain import (
    CreateModelRequest,
    CreateModelRevisionRequest,
    ModelRevisionInput,
    UpdateModelRequest,
    WorkspaceSecretCredential,
)
from a13n_service.models.endpoint_policy import EndpointPolicy
from a13n_service.models.models import ModelRecord, ModelRevisionRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.service import ModelError, ModelService
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session, transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
VIEWER_ID = "usr_abcdef1234567890"
SECRET_ID = "sec_1234567890abcdef"


async def successful_connection_test(**_: object) -> None:
    return None


@pytest.fixture
async def model_service(
    service_sqlite_database: Path,
) -> AsyncIterator[tuple[ModelService, AsyncEngine]]:
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
        for user_id in (USER_ID, VIEWER_ID):
            session.add(
                UserRecord(
                    id=user_id,
                    email=f"{user_id}@example.com",
                    normalized_email=f"{user_id}@example.com",
                    name=user_id,
                    status="active",
                    email_verified_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.flush()
        for user_id in (USER_ID, VIEWER_ID):
            session.add(
                RoleBindingRecord(
                    id=f"rb_{user_id[-16:]}",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=user_id,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_builder1234567890",
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
                RoleBindingRecord(
                    id="rb_viewer12345678901",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=VIEWER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="viewer",
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
            )
        )
    service = ModelService(
        sessions,
        built_in_provider_registry(),
        EndpointPolicy(),
        clock=lambda: NOW,
        resolve_dns_on_save=False,
        connection_tester=successful_connection_test,
    )
    try:
        yield service, engine
    finally:
        await engine.dispose()


def actor(user_id: str = USER_ID) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=user_id),
        auth_method="api_key",
        credential_id="key_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="request-1",
    )


def config(*, model_name: str = "gpt-5.6-terra") -> ModelRevisionInput:
    return ModelRevisionInput(
        provider_type="openai",
        model_name=model_name,
        credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
        provider_config={},
    )


def create_request(*, name: str = "Primary", model_name: str = "gpt-5.6-terra") -> CreateModelRequest:
    return CreateModelRequest(name=name, config=config(model_name=model_name))


@pytest.mark.anyio
async def test_create_commits_head_and_v1_revision_atomically(
    model_service: tuple[ModelService, AsyncEngine],
) -> None:
    service, engine = model_service

    result = await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request())

    assert result.model.version == result.revision.version == 1
    assert result.model.current_revision_id == result.revision.id
    assert result.revision.base_url == "https://api.openai.com/v1"
    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        assert await session.scalar(select(func.count()).select_from(ModelRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(ModelRevisionRecord)) == 1
        audit = await session.scalar(select(SecurityAuditRecord))
        assert audit is not None and audit.action == "model.create"


@pytest.mark.anyio
async def test_metadata_update_uses_etag_without_incrementing_version(
    model_service: tuple[ModelService, AsyncEngine],
) -> None:
    service, _ = model_service
    created = (await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request())).model
    etag = resource_etag(created.id, created.updated_at)

    updated = await service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=created.id,
        if_match=etag,
        request=UpdateModelRequest(name="Renamed", enabled=False),
    )

    assert updated.name == "Renamed"
    assert not updated.enabled
    assert updated.version == created.version
    with pytest.raises(ModelError, match="changed after") as stale:
        await service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            model_id=created.id,
            if_match='"stale"',
            request=UpdateModelRequest(name="Stale"),
        )
    assert stale.value.status_code == 412


@pytest.mark.anyio
async def test_new_revision_increments_version_and_semantic_noop_does_not(
    model_service: tuple[ModelService, AsyncEngine],
) -> None:
    service, _ = model_service
    created = await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request())

    noop = await service.create_revision(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=created.model.id,
        request=CreateModelRevisionRequest(expected_version=1, config=config()),
    )
    changed = await service.create_revision(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=created.model.id,
        request=CreateModelRevisionRequest(expected_version=1, config=config(model_name="gpt-5.6-sol")),
    )

    assert noop.revision.id == created.revision.id
    assert noop.model.version == 1
    assert changed.model.version == changed.revision.version == 2
    assert changed.model.current_revision_id == changed.revision.id


@pytest.mark.anyio
async def test_name_uniqueness_and_credential_eligibility(
    model_service: tuple[ModelService, AsyncEngine],
) -> None:
    service, _ = model_service
    await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request())
    with pytest.raises(ModelError) as duplicate:
        await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(name="PRIMARY"))
    assert duplicate.value.code == "model_name_conflict"

    missing = create_request(name="Missing").model_copy(
        update={
            "config": config().model_copy(
                update={"credential": WorkspaceSecretCredential(secret_id="sec_abcdef1234567890")}
            )
        }
    )
    with pytest.raises(ModelError) as ineligible:
        await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=missing)
    assert ineligible.value.code == "credential_not_eligible"


@pytest.mark.anyio
async def test_viewer_can_read_but_cannot_create(
    model_service: tuple[ModelService, AsyncEngine],
) -> None:
    service, _ = model_service
    created = (await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request())).model
    assert (await service.get(actor=actor(VIEWER_ID), workspace_id=WORKSPACE_ID, model_id=created.id)).id == created.id
    with pytest.raises(ModelError) as denied:
        await service.create(actor=actor(VIEWER_ID), workspace_id=WORKSPACE_ID, request=create_request(name="Denied"))
    assert denied.value.status_code == 404


@pytest.mark.anyio
async def test_list_filters_by_current_revision_provider(
    model_service: tuple[ModelService, AsyncEngine],
) -> None:
    service, _ = model_service
    await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request())
    page = await service.list(actor=actor(), workspace_id=WORKSPACE_ID, provider_type="openai", enabled=True)
    assert [item.name for item in page.items] == ["Primary"]


@pytest.mark.anyio
async def test_candidate_connection_test_returns_only_safe_result(
    model_service: tuple[ModelService, AsyncEngine],
) -> None:
    service, _ = model_service
    result = await service.test_candidate(actor=actor(), workspace_id=WORKSPACE_ID, request=config())
    assert result.success
    assert result.code == "connection_succeeded"
    assert result.may_consume_quota_or_incur_cost
