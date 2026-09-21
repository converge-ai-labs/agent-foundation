from __future__ import annotations

import asyncio

import pytest
from a13n_service.agents.invocation_resolution.media import MediaUnderstandingResolution
from a13n_service.etags import resource_etag
from a13n_service.iam.models import RoleBindingRecord, SecurityAuditRecord
from a13n_service.models.domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    MediaUnderstandingSelection,
    ModelDeclarations,
    UpdateModelRequest,
)
from a13n_service.models.models import MediaUnderstandingDefaultsRecord
from a13n_service.models.providers import built_in_model_provider_catalog
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service_common import ModelError
from a13n_service.storage import short_session, transaction
from sqlalchemy import select

from .conftest import ORG_ID, WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


async def create_vision(provider_service, model_service):
    provider = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="Vision", credential={"api_key": "test-only"}),
    )
    return await model_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelRequest(
            key="vision",
            name="Vision",
            provider_id=provider.id,
            upstream_model="vision-v1",
            model_api="openai.responses",
            settings={"temperature": 0.7},
            declarations=ModelDeclarations(capabilities={"image_understanding", "video_understanding"}),
        ),
    )


async def test_defaults_save_clear_audit_and_scope(model_service, provider_service, model_sessions):
    initial = await model_service.media_defaults(actor=actor(), workspace_id=WORKSPACE_ID)
    assert initial.version == 0 and initial.selections() == {}
    async with short_session(model_sessions) as session:
        assert await session.get(MediaUnderstandingDefaultsRecord, WORKSPACE_ID) is None
    model = await create_vision(provider_service, model_service)
    saved = await model_service.replace_media_defaults(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=MediaUnderstandingSelection(image=model.key, video=model.key),
        if_match=initial.etag(),
    )
    assert saved.version == 1 and saved.image == "vision" and saved.video == "vision"
    with pytest.raises(ModelError, match="changed") as stale:
        await model_service.replace_media_defaults(
            actor=actor(), workspace_id=WORKSPACE_ID, request=MediaUnderstandingSelection(), if_match=initial.etag()
        )
    assert stale.value.code == "precondition_failed"
    with pytest.raises(ModelError) as missing:
        await model_service.media_defaults(actor=actor(), workspace_id="ws_missing1234567890")
    assert missing.value.code == "resource_not_found"
    cleared = await model_service.replace_media_defaults(
        actor=actor(), workspace_id=WORKSPACE_ID, request=MediaUnderstandingSelection(), if_match=saved.etag()
    )
    assert cleared.version == 2 and cleared.selections() == {}
    async with short_session(model_sessions) as session:
        events = (
            await session.scalars(
                select(SecurityAuditRecord).where(SecurityAuditRecord.action == "media_understanding_defaults.update")
            )
        ).all()
        assert len(events) == 2


async def test_defaults_reject_unsupported_missing_and_disabled_models(model_service, provider_service):
    model = await create_vision(provider_service, model_service)
    initial = await model_service.media_defaults(actor=actor(), workspace_id=WORKSPACE_ID)
    for selection, code in [
        (MediaUnderstandingSelection(audio=model.key), "model_media_capability_required"),
        (MediaUnderstandingSelection(image="missing"), "model_not_found"),
    ]:
        with pytest.raises(ModelError) as failure:
            await model_service.replace_media_defaults(
                actor=actor(), workspace_id=WORKSPACE_ID, request=selection, if_match=initial.etag()
            )
        assert failure.value.code == code
    await model_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=model.id,
        if_match=resource_etag(model.id, model.updated_at),
        request=UpdateModelRequest(enabled=False),
    )
    with pytest.raises(ModelError) as disabled:
        await model_service.replace_media_defaults(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=MediaUnderstandingSelection(image=model.key),
            if_match=initial.etag(),
        )
    assert disabled.value.code == "model_disabled"
    assert (await model_service.media_defaults(actor=actor(), workspace_id=WORKSPACE_ID)).version == 0


async def test_defaults_concurrent_initial_write_and_read_only_permission(model_service, model_sessions):
    initial = await model_service.media_defaults(actor=actor(), workspace_id=WORKSPACE_ID)
    results = await asyncio.gather(
        *[
            model_service.replace_media_defaults(
                actor=actor(), workspace_id=WORKSPACE_ID, request=MediaUnderstandingSelection(), if_match=initial.etag()
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, ModelError) and result.code == "precondition_failed" for result in results) == 1
    async with transaction(model_sessions) as session:
        role = await session.scalar(select(RoleBindingRecord).where(RoleBindingRecord.workspace_id == WORKSPACE_ID))
        role.role_key = "viewer"
    saved = await model_service.media_defaults(actor=actor(), workspace_id=WORKSPACE_ID)
    with pytest.raises(ModelError) as forbidden:
        await model_service.replace_media_defaults(
            actor=actor(), workspace_id=WORKSPACE_ID, request=MediaUnderstandingSelection(), if_match=saved.etag()
        )
    assert forbidden.value.code == "resource_not_found"


async def test_prepared_media_keeps_saved_settings_after_model_and_defaults_edit(
    model_service, provider_service, model_sessions
):
    model = await create_vision(provider_service, model_service)
    initial = await model_service.media_defaults(actor=actor(), workspace_id=WORKSPACE_ID)
    saved = await model_service.replace_media_defaults(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=MediaUnderstandingSelection(image=model.key),
        if_match=initial.etag(),
    )
    selector = AcceptedModelSelector(model_sessions, built_in_model_provider_catalog())

    def resolution() -> MediaUnderstandingResolution:
        return MediaUnderstandingResolution(model_sessions, selector, organization_id=ORG_ID, workspace_id=WORKSPACE_ID)

    prepared = await resolution().resolve(MediaUnderstandingSelection())
    await model_service.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=model.id,
        if_match=resource_etag(model.id, model.updated_at),
        request=UpdateModelRequest(settings={"temperature": 0.1}, upstream_model="vision-v2"),
    )
    await model_service.replace_media_defaults(
        actor=actor(), workspace_id=WORKSPACE_ID, request=MediaUnderstandingSelection(), if_match=saved.etag()
    )
    assert prepared["image"].resource.settings == {"temperature": 0.7}
    assert prepared["image"].resource.upstream_model == "vision-v1"
    assert await resolution().resolve(MediaUnderstandingSelection()) == {}


async def test_defaults_allow_shared_models_but_not_sibling_models(model_service, provider_service, model_sessions):
    from ..resource_scope_helpers import organization_admin, sibling_workspace

    admin = await organization_admin(model_sessions, actor())
    sibling = await sibling_workspace(model_sessions, admin)
    provider = await provider_service.create(
        actor=admin,
        workspace_id=None,
        request=CreateModelProviderRequest(
            type="openai",
            name="Shared",
            credential={"api_key": "test"},
        ),
    )
    for scope, key in [(None, "shared-vision"), (sibling, "sibling-vision")]:
        await model_service.create(
            actor=admin,
            workspace_id=scope,
            request=CreateModelRequest(
                key=key,
                name=key,
                provider_id=provider.id,
                upstream_model="vision",
                model_api="openai.responses",
                declarations=ModelDeclarations(capabilities={"image_understanding"}),
            ),
        )
    initial = await model_service.media_defaults(actor=actor(), workspace_id=WORKSPACE_ID)
    with pytest.raises(ModelError) as hidden:
        await model_service.replace_media_defaults(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=MediaUnderstandingSelection(image="sibling-vision"),
            if_match=initial.etag(),
        )
    assert hidden.value.code == "model_not_found"
    saved = await model_service.replace_media_defaults(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=MediaUnderstandingSelection(image="shared-vision"),
        if_match=initial.etag(),
    )
    assert saved.image == "shared-vision"
    assert (await model_service.media_defaults(actor=admin, workspace_id=sibling)).selections() == {}
