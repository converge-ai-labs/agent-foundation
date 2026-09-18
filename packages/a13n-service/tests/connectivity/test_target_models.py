"""Account Target model overrides share the workspace-visible Model namespace."""

from dataclasses import replace

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.agents.domain import ModelOverride
from a13n_service.connectivity.accounts.reception import InputOverride
from a13n_service.connectivity.accounts.targets import ReplaceTargetRequest, TargetConfig
from a13n_service.connectivity.errors import NativeError
from a13n_service.http_errors import application_error_status
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord
from a13n_service.models.domain import CreateModelProviderRequest, CreateModelRequest
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.service import ModelService
from a13n_service.storage import transaction

from ..resource_scope_helpers import organization_admin, sibling_workspace
from .conftest import ACCOUNT_ID, NOW, USER_ID, WORKSPACE_ID, actor


@pytest.fixture
async def selected_model(request, connectivity_sessions, credential_protector):
    scenario = request.param
    admin = await organization_admin(connectivity_sessions, actor())
    workspace_id = WORKSPACE_ID if scenario == "workspace" else None
    if scenario == "sibling":
        workspace_id = await sibling_workspace(connectivity_sessions, admin)
    if scenario == "foreign_organization":
        organization_id = "org_foreign1234567890"
        async with transaction(connectivity_sessions) as session:
            session.add(
                OrganizationRecord(id=organization_id, key="foreign", name="Foreign", created_at=NOW, updated_at=NOW)
            )
            await session.flush()
            session.add(
                RoleBindingRecord(
                    id="rb_foreign1234567890",
                    organization_id=organization_id,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="organization",
                    resource_id=organization_id,
                    role_key="admin",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        admin = replace(admin, boundary_organization_id=organization_id)
    registry = built_in_provider_registry()
    providers = ModelProviderService(
        connectivity_sessions,
        registry,
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
        credential_protector,
        resolve_dns_on_save=False,
    )
    provider = await providers.create(
        actor=admin,
        workspace_id=None,
        request=CreateModelProviderRequest(type="openai", name="Shared", credential="sk-test"),
    )
    models = ModelService(connectivity_sessions, registry)
    model = await models.create(
        actor=admin,
        workspace_id=workspace_id,
        request=CreateModelRequest(
            provider_id=provider.id,
            key="coding",
            name="Coding",
            upstream_model="gpt-test",
            model_api="openai.responses",
        ),
    )
    if scenario in {"disabled_model", "disabled_provider"}:
        async with transaction(connectivity_sessions) as session:
            record = (
                await session.get(ModelRecord, model.id)
                if scenario == "disabled_model"
                else await session.get(ModelProviderRecord, provider.id)
            )
            record.enabled = False
    return model


@pytest.mark.parametrize("selected_model", ["organization", "workspace"], indirect=True)
async def test_target_create_and_replace_accept_visible_model(target_service, selected_model):
    override = InputOverride(model=ModelOverride(model_key=selected_model.key.upper()))
    target = await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="target-model",
        request=TargetConfig(target_kind="conversation", external_target_id="support", config_override=override),
    )
    assert target.config_override == override
    updated = await target_service.replace(
        actor=actor(),
        account_id=ACCOUNT_ID,
        target_id=target.id,
        request=ReplaceTargetRequest(
            expected_version=target.version,
            target_kind="conversation",
            external_target_id="support",
            config_override=override,
        ),
    )
    assert updated.config_override == override


@pytest.mark.parametrize(
    "selected_model", ["sibling", "foreign_organization", "disabled_model", "disabled_provider"], indirect=True
)
async def test_target_create_and_replace_reject_unavailable_model(target_service, selected_model):
    override = InputOverride(model=ModelOverride(model_key=selected_model.key))
    config = TargetConfig(target_kind="conversation", external_target_id="support", config_override=override)
    with pytest.raises(NativeError) as error:
        await target_service.create(
            actor=actor(), account_id=ACCOUNT_ID, idempotency_key="invalid-model", request=config
        )
    assert error.value.code == "invalid_model_selection" and application_error_status(error.value) == 400
    target = await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="target",
        request=TargetConfig(target_kind="conversation", external_target_id="support"),
    )
    with pytest.raises(NativeError) as error:
        await target_service.replace(
            actor=actor(),
            account_id=ACCOUNT_ID,
            target_id=target.id,
            request=ReplaceTargetRequest(expected_version=target.version, **config.model_dump(exclude_unset=True)),
        )
    assert error.value.code == "invalid_model_selection" and application_error_status(error.value) == 400
