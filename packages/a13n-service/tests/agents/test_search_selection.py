import pytest
from a13n_service.agents.domain import AgentConfig, AgentRunOverride, ChildAgentExecution, CreateAgentRequest
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation import merge_agent_run_override
from a13n_service.digests import digest_request
from a13n_service.etags import resource_etag
from a13n_service.search.domain import CreateSearchProviderRequest, SearchSelection, UpdateSearchProviderRequest
from a13n_service.search.runtime import graph_uses_search
from a13n_service.search.service import SearchProviderService
from a13n_service.storage import transaction

from ..models.conftest import protector
from .conftest import WORKSPACE_ID, actor, agent_config
from .test_reconstruction import _effective, _reconstruct


def test_search_override_preserves_omission_null_and_complete_replacement():
    selection = SearchSelection(provider_id="sprov_1234567890abcdef", max_results=9, include_domains=("example.com",))
    base = agent_config().model_copy(update={"search": selection})
    assert merge_agent_run_override(base, AgentRunOverride()).search == selection
    assert merge_agent_run_override(base, AgentRunOverride(search=None)).search is None
    replaced = merge_agent_run_override(
        base, AgentRunOverride(search=SearchSelection(provider_id=selection.provider_id))
    ).search
    assert replaced.max_results == 5 and replaced.include_domains == ()
    assert "search" not in AgentRunOverride().model_dump(exclude_unset=True)
    assert AgentRunOverride(search=None).model_dump(exclude_unset=True) == {"search": None}


def test_absent_search_preserves_old_effective_configuration_digest():
    old = _effective(agent_config())
    payload = old.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    assert "search" not in payload
    assert digest_request(payload) == old.content_digest
    _reconstruct(old)
    config = AgentConfig.model_validate(agent_config().model_dump(mode="json", by_alias=True))
    assert config.search is None and "search" not in config.model_dump(mode="json", by_alias=True)


@pytest.mark.anyio
async def test_selection_authoring_freezing_and_live_changes(
    agent_sessions, agent_management, agent_invocation_resolver
):
    providers = SearchProviderService(agent_sessions, protector())
    account = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSearchProviderRequest(type="brave", name="Search", credential="secret"),
    )
    selection = SearchSelection(provider_id=account.id)
    base = agent_config().model_copy(update={"search": selection})
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="search-agent",
        request=CreateAgentRequest(name="Search", config=base),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.search == selection
    assert "secret" not in frozen.effective_config.model_dump_json(exclude={"secret_requirements"})
    assert (await providers.references(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=account.id)).items[
        0
    ].agent_revision_id == created.revision.id
    await providers.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=account.id,
        if_match=resource_etag(account.id, account.updated_at),
        request=UpdateSearchProviderRequest(enabled=False),
    )
    assert frozen.effective_config.search == selection
    with pytest.raises(Exception) as disabled:
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert disabled.value.code == "search_provider_disabled"
    with pytest.raises(AgentError) as invalid:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="disabled-search-agent",
            request=CreateAgentRequest(name="Disabled", config=base),
        )
    assert invalid.value.code == "search_provider_disabled"


def test_runtime_requirement_includes_deeply_nested_child_search():
    empty = _effective(agent_config())
    assert not graph_uses_search(empty)
    node = empty.model_copy(update={"search": SearchSelection(provider_id="sprov_1234567890abcdef")})
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
    assert node.search is None
    assert graph_uses_search(node)
