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
        request=CreateModelProviderRequest(type="openai", name=name, credential="sk-secret"),
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
        request=UpdateModelProviderRequest(credential="sk-rotated"),
    )
    assert updated.credential_configured is True
    async with transaction(model_sessions) as session:
        stored = await session.get(ModelProviderRecord, provider.id)
        assert stored is not None
        assert stored.credential_version == 2
        assert bytes(stored.ciphertext or b"") != first_ciphertext


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_type", "config", "credential"),
    [
        ("openai", {}, ""),
        ("aws_bedrock", {"region": "us-east-1"}, "{}"),
        ("google_vertex", {"project_id": "project", "location": "us-central1"}, "{}"),
    ],
)
async def test_provider_rejects_invalid_credential_before_storage(
    provider_service: ModelProviderService,
    provider_type: str,
    config: dict[str, object],
    credential: str,
) -> None:
    with pytest.raises(ModelError) as rejected:
        await provider_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateModelProviderRequest(
                type=provider_type,
                name=f"Invalid {provider_type}",
                config=config,
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
            model_apis=({"api": "openai.responses"},),
        ),
    )

    updated = await model_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=model.id,
        if_match=resource_etag(model.id, model.updated_at),
        request=UpdateModelRequest(
            upstream_model="gpt-new",
            model_apis=({"api": "openai.chat_completions"},),
        ),
    )
    assert updated.key == "support/main"
    assert updated.provider_id == provider.id
    assert updated.upstream_model == "gpt-new"
    assert updated.model_apis[0].api == "openai.chat_completions"

    with pytest.raises(ModelError, match="key"):
        await model_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateModelRequest(
                key="SUPPORT/MAIN",
                provider_id=provider.id,
                name="Duplicate",
                upstream_model="gpt-other",
                model_apis=({"api": "openai.responses"},),
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
                model_apis=({"api": "anthropic.messages"},),
            ),
        )
    assert rejected.value.code == "invalid_model_apis"
