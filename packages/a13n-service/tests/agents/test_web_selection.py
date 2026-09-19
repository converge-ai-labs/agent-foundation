import pytest
from a13n_service.agents.domain import AgentConfig, AgentRunOverride, ChildAgentExecution, CreateAgentRequest
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation import merge_agent_run_override
from a13n_service.digests import digest_request
from a13n_service.etags import resource_etag
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.storage import transaction
from a13n_service.web.domain import CreateWebProviderRequest, UpdateWebProviderRequest
from a13n_service.web.runtime import graph_uses_web
from a13n_service.web.service import WebProviderService

from ..models.conftest import protector
from .conftest import WORKSPACE_ID, actor, agent_config
from .test_reconstruction import _effective, _reconstruct


def _with_web(config: AgentConfig, **tools: dict[str, object]) -> AgentConfig:
    payload = config.model_dump(mode="python", by_alias=True)
    payload["toolsets"] = {
        **payload["toolsets"],
        "web": {
            "enabled": True,
            "tools": {key: {"permission": "allow", **selection} for key, selection in tools.items()},
        },
    }
    return AgentConfig.model_validate(payload)


def test_web_override_preserves_omission_and_whole_entry_replacement():
    provider_id = "wprov_1234567890abcdef"
    base = _with_web(
        agent_config(),
        search={"config": {"provider_id": provider_id, "max_results": 9, "allow_domains": ["example.com"]}},
    )
    assert merge_agent_run_override(base, AgentRunOverride()).toolsets == base.toolsets
    replaced = merge_agent_run_override(
        base,
        AgentRunOverride.model_validate(
            {"toolsets": {"web": {"enabled": False, "tools": {"search": {"enabled": False}}}}}
        ),
    ).toolsets["web"]
    assert not replaced.enabled and not replaced.tools["search"].enabled
    assert replaced.tools["search"].config == {"max_results": 5}
    assert "toolsets" not in AgentRunOverride().model_dump(exclude_unset=True)


def test_default_toolsets_are_explicit_in_effective_configuration_digest():
    old = _effective(agent_config())
    payload = old.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    assert payload["toolsets"]["web"]["enabled"] is False
    assert digest_request(payload) == old.content_digest
    _reconstruct(old)
    config = AgentConfig.model_validate(agent_config().model_dump(mode="json", by_alias=True))
    assert config.toolsets["web"].enabled is False


@pytest.mark.anyio
async def test_selection_authoring_freezing_and_live_changes(
    agent_sessions, agent_management, agent_invocation_resolver
):
    providers = WebProviderService(agent_sessions, protector(), load_provider_catalogs(()).web)
    account = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(type="brave", name="Search", credential={"api_key": "secret"}),
    )
    base = _with_web(agent_config(), search={"config": {"provider_id": account.id}})
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="search-agent",
        request=CreateAgentRequest(name="Search", config=base),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.toolsets["web"] == base.toolsets["web"]
    assert "secret" not in frozen.effective_config.model_dump_json(exclude={"secret_requirements"})
    assert (await providers.references(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=account.id)).items[
        0
    ].agent_revision_id == created.revision.id
    await providers.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        if_match=resource_etag(account.id, account.updated_at),
        request=UpdateWebProviderRequest(enabled=False),
    )
    assert frozen.effective_config.toolsets["web"] == base.toolsets["web"]
    with pytest.raises(Exception) as disabled:
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert disabled.value.code == "web_provider_disabled"
    with pytest.raises(AgentError) as invalid:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="disabled-search-agent",
            request=CreateAgentRequest(name="Disabled", config=base),
        )
    assert invalid.value.code == "web_provider_disabled"


@pytest.mark.anyio
async def test_search_and_scrape_select_accounts_independently(agent_sessions, agent_management):
    providers = WebProviderService(agent_sessions, protector(), load_provider_catalogs(()).web)
    brave = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(type="brave", name="Brave", credential={"api_key": "brave-secret"}),
    )
    exa = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(type="exa", name="Exa", credential={"api_key": "exa-secret"}),
    )
    selected = _with_web(
        agent_config(),
        search={"config": {"provider_id": brave.id}},
        scrape={"config": {"provider_id": exa.id}},
    )
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="separate-web-accounts",
        request=CreateAgentRequest(name="Web", config=selected),
    )
    assert created.revision.config.toolsets["web"] == selected.toolsets["web"]
    shared = _with_web(
        agent_config(),
        search={"config": {"provider_id": exa.id}},
        scrape={"config": {"provider_id": exa.id}},
    )
    shared_created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="shared-web-account",
        request=CreateAgentRequest(name="Shared Web", config=shared),
    )
    assert shared_created.revision.config.toolsets["web"] == shared.toolsets["web"]


@pytest.mark.anyio
async def test_scrape_rejects_search_only_provider(agent_sessions, agent_management):
    providers = WebProviderService(agent_sessions, protector(), load_provider_catalogs(()).web)
    brave = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateWebProviderRequest(type="brave", name="Brave", credential={"api_key": "secret"}),
    )
    with pytest.raises(AgentError) as invalid:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="unsupported-brave-scrape",
            request=CreateAgentRequest(
                name="Invalid scrape",
                config=_with_web(agent_config(), scrape={"config": {"provider_id": brave.id}}),
            ),
        )
    assert invalid.value.code == "web_provider_operation_unsupported"


def test_runtime_requirement_includes_deeply_nested_child_web_selection():
    empty = _effective(agent_config())
    assert not graph_uses_web(empty)
    selected = _with_web(
        agent_config(),
        search={"config": {"provider_id": "wprov_1234567890abcdef"}},
    )
    node = _effective(selected)
    for _ in range(3):
        node = empty.model_copy(
            update={
                "child_configs": {
                    "arev_1234567890abcdef": ChildAgentExecution(
                        agent_id="agent_1234567890abcdef", revision_content_digest="0" * 64, effective_config=node
                    )
                }
            }
        )
    assert not node.toolsets["web"].enabled
    assert graph_uses_web(node)
