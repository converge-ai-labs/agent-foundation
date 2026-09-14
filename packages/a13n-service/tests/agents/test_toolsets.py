import pytest
from a13n_service.agents.toolset_service import ToolsetCandidate
from a13n_service.agents.toolsets import default_toolsets
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.web.domain import CreateWebProviderRequest
from a13n_service.web.registry import WebProviderRegistry
from a13n_service.web.service import WebProviderService
from pydantic import ValidationError

from ..models.conftest import protector
from .conftest import MODEL_ID, WORKSPACE_ID, actor


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
    assert defaults["files"].tools["view"].permission == "auto"
    assert not defaults["web"].tools["search"].enabled

    with pytest.raises(ValidationError):
        ToolsetCandidate.model_validate({"toolsets": {"unknown": {}}})
    auto = ToolsetCandidate.model_validate({"toolsets": {"files": {"tools": {"view": {"permission": "auto"}}}}})
    assert auto.toolsets["files"].tools["view"].permission == "auto"
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
    assert definitions.items[0].tools[0].default_permission == "review"
    assert "auto" in definitions.items[0].tools[0].supported_permissions

    auto_without_reviewer = ToolsetCandidate.model_validate(
        {"toolsets": {"files": {"tools": {"view": {"permission": "auto"}}}}}
    )
    missing_reviewer = await agent_management.toolsets.validate_candidate(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        candidate=auto_without_reviewer,
    )
    assert missing_reviewer.errors[0].code == "tool_reviewer_missing"

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
        WebProviderRegistry(load_provider_catalogs(()).web),
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
