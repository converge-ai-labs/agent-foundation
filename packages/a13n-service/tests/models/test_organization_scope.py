from dataclasses import replace
from unittest.mock import patch

import pytest
from a13n_service.etags import resource_etag
from a13n_service.models.credentials import ProviderSecrets
from a13n_service.models.domain import (
    CreateModelProviderRequest,
    CreateModelRequest,
    UpdateModelProviderRequest,
    UpdateModelRequest,
)
from a13n_service.models.models import ModelProviderRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service_common import ModelError
from a13n_service.secrets.crypto import SecretProtectionError
from a13n_service.storage import short_session

from ..resource_scope_helpers import organization_admin, sibling_workspace
from .conftest import ORG_ID, WORKSPACE_ID, actor, protector

pytestmark = pytest.mark.anyio


@pytest.fixture
async def org_admin(model_sessions):
    return await organization_admin(model_sessions, actor())


@pytest.fixture
async def org_provider(provider_service, org_admin):
    return await provider_service.create(
        actor=org_admin,
        workspace_id=None,
        request=CreateModelProviderRequest(type="openai", name="Shared", credential="sk-shared"),
    )


def model_request(provider_id: str, key: str = "coding") -> CreateModelRequest:
    return CreateModelRequest(
        provider_id=provider_id, key=key, name=key, upstream_model="gpt-test", model_api="openai.responses"
    )


async def test_shared_models_resolve_by_bare_key_and_use_owned_credentials(
    model_service, provider_service, model_sessions, org_admin, org_provider
):
    model = await model_service.create(actor=org_admin, workspace_id=None, request=model_request(org_provider.id))
    assert model.workspace_id is None
    assert (await model_service.list(actor=actor(), workspace_id=WORKSPACE_ID)).items == (model,)
    assert (await provider_service.list(actor=actor(), workspace_id=WORKSPACE_ID)).items == (org_provider,)
    prepared = await AcceptedModelSelector(model_sessions, built_in_provider_registry()).prepare(
        organization_id=ORG_ID, workspace_id=WORKSPACE_ID, model_key="coding", settings={}
    )
    assert prepared.resource.id == model.id
    assert prepared.workspace_id == WORKSPACE_ID
    async with short_session(model_sessions) as session:
        record = await session.get(ModelProviderRecord, org_provider.id)
        assert record is not None
        snapshot = record.credential_snapshot()
        assert ProviderSecrets.model_validate_json(snapshot.decrypt(protector())).credential == "sk-shared"
        with pytest.raises(SecretProtectionError):
            replace(snapshot, workspace_id=WORKSPACE_ID).decrypt(protector())
        with pytest.raises(SecretProtectionError):
            replace(snapshot, workspace_id="").decrypt(protector())
    with pytest.raises(ModelError):
        await model_service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            model_id=model.id,
            if_match=resource_etag(model.id, model.updated_at),
            request=UpdateModelRequest(enabled=False),
        )
    with (
        pytest.raises(ModelError),
        patch(
            "a13n_service.models.provider_service.CredentialSnapshot.decrypt",
            side_effect=AssertionError("must authorize first"),
        ),
    ):
        await provider_service.update(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            provider_id=org_provider.id,
            if_match=resource_etag(org_provider.id, org_provider.updated_at),
            request=UpdateModelProviderRequest(credential="replacement"),
        )


@pytest.mark.parametrize("organization_first", [True, False])
async def test_model_key_conflicts_in_both_directions(model_service, org_admin, org_provider, organization_first):
    first_actor, first_scope = (org_admin, None) if organization_first else (actor(), WORKSPACE_ID)
    second_actor, second_scope = (actor(), WORKSPACE_ID) if organization_first else (org_admin, None)
    await model_service.create(actor=first_actor, workspace_id=first_scope, request=model_request(org_provider.id))
    with pytest.raises(ModelError) as error:
        await model_service.create(
            actor=second_actor, workspace_id=second_scope, request=model_request(org_provider.id, "CODING")
        )
    assert error.value.code == "model_key_conflict"


async def test_sibling_workspace_keys_are_independent_but_block_org_key(
    model_service, model_sessions, org_admin, org_provider
):
    sibling = await sibling_workspace(model_sessions, org_admin)
    first = await model_service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=model_request(org_provider.id))
    second = await model_service.create(actor=org_admin, workspace_id=sibling, request=model_request(org_provider.id))
    assert first.id != second.id
    assert (await model_service.list(actor=actor(), workspace_id=WORKSPACE_ID)).items == (first,)
    with pytest.raises(ModelError):
        await model_service.get(actor=actor(), workspace_id=WORKSPACE_ID, model_id=second.id)
    with pytest.raises(ModelError) as error:
        await model_service.create(actor=org_admin, workspace_id=None, request=model_request(org_provider.id))
    assert error.value.code == "model_key_conflict"


async def test_org_cannot_reference_workspace_provider(model_service, provider_service, org_admin):
    local = await provider_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateModelProviderRequest(type="openai", name="Local", credential="sk-local"),
    )
    with pytest.raises(ModelError):
        await model_service.create(actor=org_admin, workspace_id=None, request=model_request(local.id))


async def test_scope_creation_requires_organization_admin(model_service, provider_service):
    with pytest.raises(ModelError):
        await provider_service.create(
            actor=actor(),
            workspace_id=None,
            request=CreateModelProviderRequest(type="openai", name="Denied", credential="sk-denied"),
        )
    with pytest.raises(ModelError):
        await provider_service.create(
            actor=replace(actor(), boundary_workspace_id=None, boundary_organization_id=ORG_ID),
            workspace_id=None,
            request=CreateModelProviderRequest(type="openai", name="Denied", credential="sk-denied"),
        )


async def test_cross_organization_resources_remain_invisible(
    model_sessions, model_service, provider_service, org_admin, org_provider
):
    from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord
    from a13n_service.ids import new_object_id
    from a13n_service.storage import transaction

    from .conftest import NOW, USER_ID

    other_id = new_object_id("org")
    async with transaction(model_sessions) as session:
        session.add(OrganizationRecord(id=other_id, key="other", name="Other", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            RoleBindingRecord(
                id=new_object_id("rb"),
                organization_id=other_id,
                workspace_id=None,
                principal_type="user",
                principal_id=USER_ID,
                resource_type="organization",
                resource_id=other_id,
                role_key="admin",
                created_by_user_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    other_admin = replace(org_admin, boundary_organization_id=other_id)
    other_provider = await provider_service.create(
        actor=other_admin,
        workspace_id=None,
        request=CreateModelProviderRequest(type="openai", name="Shared", credential="sk-other"),
    )
    other_model = await model_service.create(
        actor=other_admin, workspace_id=None, request=model_request(other_provider.id)
    )
    local_model = await model_service.create(actor=org_admin, workspace_id=None, request=model_request(org_provider.id))
    assert (await model_service.list(actor=actor(), workspace_id=WORKSPACE_ID)).items == (local_model,)
    with pytest.raises(ModelError):
        await model_service.get(actor=actor(), workspace_id=WORKSPACE_ID, model_id=other_model.id)
    with pytest.raises(ModelError):
        await model_service.create(
            actor=org_admin, workspace_id=None, request=model_request(other_provider.id, "other")
        )
