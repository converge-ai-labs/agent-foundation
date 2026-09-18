from __future__ import annotations

import pytest
from a13n_service.etags import resource_etag
from a13n_service.models.domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    UpdateModelProviderRequest,
    UpdateModelRequest,
)
from a13n_service.models.models import ModelProviderRecord
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.service import ModelError, ModelService
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import WORKSPACE_ID, actor


async def _create_provider(service: ModelProviderService, name: str = "OpenAI Primary"):
    return await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name=name, credential={"api_key": "sk-secret"}),
    )


@pytest.mark.anyio
async def test_workspace_can_hold_multiple_providers_of_the_same_type(
    provider_service: ModelProviderService,
) -> None:
    first = await _create_provider(provider_service, "OpenAI Production")
    second = await _create_provider(provider_service, "OpenAI Personal")

    assert first.type == second.type == "openai"
    assert first.id != second.id
    assert first.credential_configured and second.credential_configured


@pytest.mark.anyio
async def test_provider_credential_is_encrypted_write_only_and_rotatable(
    provider_service: ModelProviderService,
    model_sessions: async_sessionmaker[AsyncSession],
) -> None:
    provider = await _create_provider(provider_service)
    async with transaction(model_sessions) as session:
        stored = await session.get(ModelProviderRecord, provider.id)
        assert stored is not None
        first_ciphertext = bytes(stored.ciphertext or b"")
        assert b"sk-secret" not in first_ciphertext

    updated = await provider_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateModelProviderRequest(credential={"api_key": "sk-rotated"}),
    )
    assert updated.credential_configured is True
    async with transaction(model_sessions) as session:
        stored = await session.get(ModelProviderRecord, provider.id)
        assert stored is not None
        assert stored.credential_generation == 2
        assert bytes(stored.ciphertext or b"") != first_ciphertext


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_type", "configuration", "credential"),
    [
        ("openai", {}, {"api_key": ""}),
        ("aws_bedrock", {"region": "us-east-1"}, {}),
        ("google_vertex", {"project_id": "project", "location": "us-central1"}, {}),
    ],
)
async def test_provider_rejects_invalid_credential_before_storage(
    provider_service: ModelProviderService,
    provider_type: str,
    configuration: dict[str, object],
    credential: str,
) -> None:
    with pytest.raises(ModelError) as rejected:
        await provider_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateModelProviderRequest(
                type=provider_type,
                name=f"Invalid {provider_type}",
                configuration=configuration,
                credential=credential,
            ),
        )

    assert rejected.value.code == "invalid_provider_credential"


@pytest.mark.anyio
async def test_model_key_and_provider_are_immutable_while_content_is_mutable(
    provider_service: ModelProviderService,
    model_service: ModelService,
) -> None:
    provider = await _create_provider(provider_service)
    model = await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="support/main",
            provider_id=provider.id,
            name="Support",
            upstream_model="gpt-current",
            model_api="openai.responses",
        ),
    )

    updated = await model_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=model.id,
        if_match=resource_etag(model.id, model.updated_at),
        request=UpdateModelRequest(
            upstream_model="gpt-new",
            model_api="openai.chat_completions",
        ),
    )
    assert updated.key == "support/main"
    assert updated.provider_id == provider.id
    assert updated.upstream_model == "gpt-new"
    assert updated.model_api == "openai.chat_completions"

    with pytest.raises(ModelError, match="key"):
        await model_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateModelRequest(
                key="SUPPORT/MAIN",
                provider_id=provider.id,
                name="Duplicate",
                upstream_model="gpt-other",
                model_api="openai.responses",
            ),
        )


@pytest.mark.anyio
async def test_model_api_must_be_supported_by_provider_type(
    provider_service: ModelProviderService,
    model_service: ModelService,
) -> None:
    provider = await _create_provider(provider_service)

    with pytest.raises(ModelError) as rejected:
        await model_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateModelRequest(
                key="bad",
                provider_id=provider.id,
                name="Bad",
                upstream_model="claude",
                model_api="anthropic.messages",
            ),
        )
    assert rejected.value.code == "invalid_model_api"


@pytest.mark.anyio
async def test_model_patch_revalidates_all_settings_and_can_clear_defaults(provider_service, model_service):
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="Settings", credential={"api_key": "secret"}),
    )
    model = await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="custom-deployment",
            provider_id=provider.id,
            name="Custom",
            upstream_model="not-in-any-catalog",
            model_api="openai.responses",
            settings={"openai_text_verbosity": "low"},
        ),
    )
    with pytest.raises(ModelError) as failure:
        await model_service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            model_id=model.id,
            if_match=resource_etag(model.id, model.updated_at),
            request=UpdateModelRequest(model_api="openai.chat_completions"),
        )
    assert failure.value.code == "invalid_model_settings"
    changed = await model_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=model.id,
        if_match=resource_etag(model.id, model.updated_at),
        request=UpdateModelRequest(model_api="openai.chat_completions", settings={}),
    )
    assert changed.settings == {}
    assert changed.model_api == "openai.chat_completions"


@pytest.mark.anyio
async def test_model_identity_conflict_is_not_misreported_as_duplicate_key(
    provider_service, model_service, monkeypatch
):
    from a13n_service.models import service
    from a13n_service.models.service_common import ModelError
    from sqlalchemy.exc import IntegrityError

    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="OpenAI", credential={"api_key": "secret"}),
    )
    request = CreateModelRequest(
        key="first", provider_id=provider.id, name="First", upstream_model="gpt-next", model_api="openai.responses"
    )
    first = await model_service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request)
    with pytest.raises(ModelError) as duplicate:
        await model_service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request)
    assert duplicate.value.code == "model_key_conflict"
    monkeypatch.setattr(service, "new_model_id", lambda: first.id)
    with pytest.raises(IntegrityError):
        await model_service.create(
            actor=actor(), workspace_id=WORKSPACE_ID, request=request.model_copy(update={"key": "different"})
        )


@pytest.mark.anyio
async def test_provider_collection_cursor_roundtrips_across_pages(provider_service):
    for i in range(3):
        await provider_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateModelProviderRequest(type="openai", name=f"Account {i}", credential={"api_key": "secret"}),
        )
    first = await provider_service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=2)
    assert first.next_cursor is not None
    second = await provider_service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=2, cursor=first.next_cursor)
    assert second.next_cursor is None
    assert len({p.id for p in (*first.items, *second.items)}) == 3
