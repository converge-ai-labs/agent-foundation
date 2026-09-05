import pytest
from a13n_service.environments.domain import (
    CreateProviderRequest,
    CreateTemplateRequest,
    CreateTemplateRevisionRequest,
    NewEnvironmentSelection,
    UpdateTemplateRequest,
)
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.etags import resource_etag

from ..resource_scope_helpers import organization_admin, sibling_workspace
from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


async def test_org_template_allocates_independent_workspace_environments(
    environment_service, environment_sessions, tmp_path
):
    service = environment_service
    admin = await organization_admin(environment_sessions, actor())
    sibling = await sibling_workspace(environment_sessions, admin)
    provider = await service.create_provider(
        actor=admin,
        workspace_id=None,
        request=CreateProviderRequest(type="a13n.direct-local", name="Organization local"),
    )
    recipe = CreateTemplateRequest(
        name="Standard",
        provider_id=provider.id,
        configuration={"root": {"path": str(tmp_path)}},
        retention={"idle": {"stop_after": None, "delete_after": None}},
    )
    template = await service.create_template(
        actor=admin, workspace_id=None, request=recipe, idempotency_key="org-template"
    )
    assert template.workspace_id is None
    assert (
        await service.create_template(actor=admin, workspace_id=None, request=recipe, idempotency_key="org-template")
        == template
    )
    assert (await service.list_templates(actor=actor(), workspace_id=WORKSPACE_ID)).items == (template,)
    assert (await service.list_providers(actor=actor(), workspace_id=WORKSPACE_ID)).items == (provider,)
    choice = NewEnvironmentSelection(template_id=template.id)
    first = await service.create_environment(
        actor=actor(), workspace_id=WORKSPACE_ID, request=choice, idempotency_key="first"
    )
    second = await service.create_environment(
        actor=admin, workspace_id=sibling, request=choice, idempotency_key="second"
    )
    assert first.workspace_id == WORKSPACE_ID
    assert second.workspace_id == sibling
    assert first.id != second.id
    assert first.template_revision_id == second.template_revision_id == template.current_revision_id
    with pytest.raises(EnvironmentManagementError):
        await service.get_environment(actor=actor(), resource_id=second.id)
    with pytest.raises(EnvironmentManagementError):
        await service.update_template(
            actor=actor(),
            template_id=template.id,
            request=UpdateTemplateRequest(name="Hijacked"),
            if_match=resource_etag(template.id, template.updated_at),
        )
    revision = await service.create_revision(
        actor=admin,
        template_id=template.id,
        request=CreateTemplateRevisionRequest(
            **recipe.model_dump(exclude={"name", "description"}),
            expected_version=1,
        ),
    )
    assert revision.id == template.current_revision_id
    local = await service.create_template(
        actor=actor(), workspace_id=WORKSPACE_ID, request=recipe, idempotency_key="local-template"
    )
    assert local.workspace_id == WORKSPACE_ID


async def test_org_template_cannot_reference_local_provider(environment_service, environment_sessions, tmp_path):
    admin = await organization_admin(environment_sessions, actor())
    provider = await environment_service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.direct-local", name="Local")
    )
    with pytest.raises(EnvironmentManagementError):
        await environment_service.create_template(
            actor=admin,
            workspace_id=None,
            idempotency_key="invalid",
            request=CreateTemplateRequest(
                name="Invalid",
                provider_id=provider.id,
                configuration={"root": {"path": str(tmp_path)}},
                retention={"idle": {"stop_after": None, "delete_after": None}},
            ),
        )
