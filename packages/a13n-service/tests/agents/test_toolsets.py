import a13n_service.agents.toolset_service as toolset_service_module
import pytest
from a13n_service.agents.toolset_service import ToolsetCandidate
from a13n_service.agents.toolsets import default_toolsets
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.models.models import ModelRecord
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.storage import transaction
from a13n_service.web.domain import CreateWebProviderRequest
from a13n_service.web.models import WebProviderRecord
from a13n_service.web.service import WebProviderService
from pydantic import ValidationError

from ..models.conftest import protector
from .conftest import MODEL_ID, NOW, ORG_ID, USER_ID, WORKSPACE_ID, actor


def _provider_record(
    provider_id: str,
    *,
    organization_id: str = ORG_ID,
    workspace_id: str | None = WORKSPACE_ID,
    name: str,
) -> WebProviderRecord:
    return WebProviderRecord(
        id=provider_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        type="brave",
        name=name,
        normalized_name=name.casefold(),
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
    )


def _candidate(*, provider_id: str | None, permission: str = "allow", enabled: bool = True):
    config = {} if provider_id is None else {"provider_id": provider_id}
    return ToolsetCandidate.model_validate(
        {
            "toolsets": {
                "web": {
                    "enabled": enabled,
                    "tools": {"search": {"permission": permission, "config": config}},
                }
            },
            "reviewer": {"model": MODEL_ID},
        }
    )


def test_defaults_are_materialized_and_unknown_values_are_rejected() -> None:
    defaults = default_toolsets()
    assert set(defaults) == {"files", "shell", "web", "assets"}
    assert defaults["files"].enabled and defaults["shell"].enabled
    assert not defaults["web"].enabled and not defaults["assets"].enabled
    assert defaults["files"].tools["view"].permission == "inherit"
    assert not defaults["web"].tools["search"].enabled

    with pytest.raises(ValidationError):
        ToolsetCandidate.model_validate({"toolsets": {"unknown": {}}})
    inherited = ToolsetCandidate.model_validate({"toolsets": {"files": {"tools": {"view": {"permission": "inherit"}}}}})
    assert inherited.toolsets["files"].tools["view"].permission == "inherit"
    with pytest.raises(ValidationError):
        ToolsetCandidate.model_validate({"toolsets": {"files": {"tools": {"unknown": {}}}}})


@pytest.mark.anyio
async def test_catalog_and_candidate_validation_are_authorized_and_side_effect_free(
    agent_sessions,
    agent_management,
) -> None:
    definitions = await agent_management.toolsets.definitions(actor=actor(), workspace_id=WORKSPACE_ID)
    assert [item.key for item in definitions.items] == ["files", "shell", "web", "assets"]
    assert {tool.key for tool in definitions.items[2].tools} == {"search", "scrape", "fetch", "download"}
    assert definitions.items[0].tools[0].default_permission == "allow"
    assert "inherit" in definitions.items[0].tools[0].supported_permissions

    review_without_reviewer = ToolsetCandidate.model_validate(
        {"toolsets": {"files": {"tools": {"view": {"permission": "review"}}}}}
    )
    optional_reviewer = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=review_without_reviewer,
    )
    assert optional_reviewer.valid

    missing = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=_candidate(provider_id=None),
    )
    assert not missing.valid
    assert missing.errors[0].path == "toolsets.web.tools.search.config.provider_id"
    assert missing.errors[0].setup_destination.operation == "search"

    providers = WebProviderService(
        agent_sessions,
        protector(),
        load_provider_catalogs(()).web,
    )
    account = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(type="brave", name="Search", credential={"api_key": "secret"}),
    )
    valid = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=_candidate(provider_id=account.id, permission="review"),
    )
    assert valid.valid and not valid.errors

    disabled = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=_candidate(provider_id="wprov_missing000000000", enabled=False),
    )
    assert disabled.valid


@pytest.mark.anyio
async def test_candidate_provider_visibility_includes_org_scope_and_excludes_other_scopes(
    agent_sessions,
    agent_management,
) -> None:
    sibling_workspace_id = "ws_sibling1234567890"
    other_org_id = "org_other12345678901"
    other_workspace_id = "ws_other12345678901"
    async with transaction(agent_sessions) as session:
        session.add_all(
            (
                OrganizationRecord(id=other_org_id, key="other", name="Other", created_at=NOW, updated_at=NOW),
                WorkspaceRecord(
                    id=sibling_workspace_id,
                    organization_id=ORG_ID,
                    name="Sibling",
                    key="sibling",
                    created_at=NOW,
                    updated_at=NOW,
                    deleted_at=None,
                ),
                WorkspaceRecord(
                    id=other_workspace_id,
                    organization_id=other_org_id,
                    name="Other",
                    key="other",
                    created_at=NOW,
                    updated_at=NOW,
                    deleted_at=None,
                ),
            )
        )
        await session.flush()
        session.add_all(
            (
                _provider_record("wprov_orgshared1234567", workspace_id=None, name="Organization shared"),
                _provider_record("wprov_sibling123456789", workspace_id=sibling_workspace_id, name="Sibling only"),
                _provider_record(
                    "wprov_wrongorg12345678",
                    organization_id=other_org_id,
                    workspace_id=other_workspace_id,
                    name="Wrong organization",
                ),
            )
        )

    shared = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=_candidate(provider_id="wprov_orgshared1234567"),
    )
    assert shared.valid
    for provider_id in ("wprov_sibling123456789", "wprov_wrongorg12345678"):
        excluded = await agent_management.toolsets.validate_candidate(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            candidate=_candidate(provider_id=provider_id),
        )
        assert [error.code for error in excluded.errors] == ["web_provider_not_found"]


@pytest.mark.anyio
async def test_candidate_validates_only_supplied_reviewer_models(agent_sessions, agent_management) -> None:
    absent = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=ToolsetCandidate(),
    )
    assert absent.valid

    missing = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=ToolsetCandidate(reviewer={"model": "mdl_missing123456789"}),
    )
    assert [(error.code, error.path) for error in missing.errors] == [("model_unavailable", "reviewer.model")]

    incompatible = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=ToolsetCandidate(reviewer={"model": MODEL_ID, "model_settings": {"temperature": "bad"}}),
    )
    assert [(error.code, error.path) for error in incompatible.errors] == [
        ("invalid_model_settings", "reviewer.model_settings")
    ]

    async with transaction(agent_sessions) as session:
        model = await session.get(ModelRecord, MODEL_ID)
        assert model is not None
        model.enabled = False
    disabled = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=ToolsetCandidate(reviewer={"model": MODEL_ID}),
    )
    assert [(error.code, error.path) for error in disabled.errors] == [("model_unavailable", "reviewer.model")]


@pytest.mark.anyio
async def test_candidate_requests_web_provider_read_only_for_selected_providers(
    agent_management,
    monkeypatch,
) -> None:
    authorize_scope = toolset_service_module.authorize_scope
    actions: list[WorkspaceAction] = []

    async def tracked_authorize_scope(*args, action: WorkspaceAction, **kwargs):
        actions.append(action)
        return await authorize_scope(*args, action=action, **kwargs)

    monkeypatch.setattr(toolset_service_module, "authorize_scope", tracked_authorize_scope)

    files_only = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=ToolsetCandidate(),
    )
    assert files_only.valid
    assert actions == [WorkspaceAction.agent_read]

    actions.clear()
    await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=_candidate(provider_id="wprov_any1234567890123"),
    )
    assert actions == [WorkspaceAction.agent_read, WorkspaceAction.web_provider_read]
