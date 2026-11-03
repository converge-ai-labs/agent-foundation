"""Delta publication preserves unmentioned configuration and uses the observed head fence."""

from types import SimpleNamespace

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import AgentConfig, AgentConfigurationChanges, apply_configuration_changes
from a13n_service.resources.agents.service import change_configuration
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from pydantic import ValidationError

from .test_agents import create_agent, create_model, etag, revision_config

pytestmark = pytest.mark.anyio


def test_changes_keep_configuration_types_and_distinguish_omission_from_null():
    original = AgentConfig(
        model="before",
        instructions="Keep these instructions",
        user_questions=True,
        summarize=True,
        background_compaction={"enabled": True, "blocking_threshold": 0.92},
        model_settings={"temperature": 0.4},
        retries={"tools": 3, "output": 2},
    )
    changes = AgentConfigurationChanges.model_validate({"model": "after"})
    result = apply_configuration_changes(original, changes)
    assert result.model_dump(exclude={"model"}) == original.model_dump(exclude={"model"})
    assert result.model == "after"
    assert (
        apply_configuration_changes(original, AgentConfigurationChanges.model_validate({"retries": None})).retries
        is None
    )
    with pytest.raises(ValidationError):
        AgentConfigurationChanges.model_validate({"instructions": None})
    with pytest.raises(ValidationError):
        AgentConfigurationChanges.model_validate({"model": "invalid model key"})
    with pytest.raises(ValidationError):
        AgentConfigurationChanges.model_validate({"unknown": True})
    schema = AgentConfigurationChanges.model_json_schema()
    assert set(schema["properties"]) == set(AgentConfig.model_fields)
    assert not schema.get("required")
    assert schema["properties"]["model"]["pattern"] == AgentConfig.model_json_schema()["properties"]["model"]["pattern"]


def actor(service: SimpleNamespace) -> Principal:
    tenant = service.tenant
    return Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))


async def edit(service, head, changes, **options):
    return await change_configuration(
        service.runtime.storage,
        actor(service),
        service.tenant.workspace_id,
        head["id"],
        AgentConfigurationChanges.model_validate(changes),
        expected_version=etag(head),
        registry=service.runtime.registry,
        plugins=service.runtime.plugins,
        **options,
    )


async def test_publication_preserves_fields_and_rejects_stale_heads(service):
    first, second = await create_model(service), await create_model(service)
    head = await create_agent(service, "Delta", first, instructions="Preserve", user_questions=True, summarize=True)
    before = await revision_config(service, head)
    changed = await edit(service, head, {"model": second})
    assert changed.changed_fields == ("model",)
    assert changed.status == "published"
    assert changed.agent.default_revision_id == changed.revision.id
    assert changed.agent.version > head["version"]
    assert changed.revision.config.model_dump(exclude={"model"}) == AgentConfig.model_validate(before).model_dump(
        exclude={"model"}
    )
    with pytest.raises(ServiceError) as stale:
        await edit(service, head, {"instructions": "Must not overwrite"})
    assert stale.value.code == "precondition_failed"
    current = changed.agent.model_dump()
    unchanged = await edit(service, current, {"model": second})
    assert unchanged.changed_fields == ()
    assert unchanged.status == "unchanged"
    assert unchanged.agent.version == changed.agent.version
    assert unchanged.revision.id == changed.revision.id
    assert (await revision_config(service, current))["instructions"] == "Preserve"


async def test_historical_base_and_nondefault_publication(service):
    model = await create_model(service)
    head = await create_agent(service, "History", model, instructions="Original", user_questions=True)
    first_revision = head["default_revision_id"]
    changed = await edit(service, head, {"instructions": "Current"})
    historic = await edit(
        service, changed.agent.model_dump(), {"summarize": True}, base_revision_id=first_revision, make_default=False
    )
    assert historic.revision.config.instructions == "Original"
    assert historic.revision.config.user_questions is True
    assert historic.revision.config.summarize is True
    assert historic.agent.default_revision_id == changed.revision.id
    assert historic.agent.version > changed.agent.version
    with pytest.raises(ServiceError) as invalid:
        await edit(service, historic.agent.model_dump(), {"model": "missing-model", "instructions": "Not committed"})
    assert invalid.value.code == "invalid_argument"
    current = await service.client.get(f"{service.api}/agents/{head['id']}")
    assert current.json()["version"] == historic.agent.version


async def test_concurrent_deltas_have_one_winner(service):
    import asyncio

    model = await create_model(service)
    head = await create_agent(service, "Concurrent", model, instructions="Original")
    results = await asyncio.gather(
        edit(service, head, {"instructions": "First"}),
        edit(service, head, {"instructions": "Second"}),
        return_exceptions=True,
    )
    failures = [value for value in results if isinstance(value, ServiceError)]
    assert len(failures) == 1 and failures[0].code == "precondition_failed"
    successes = [value for value in results if not isinstance(value, BaseException)]
    assert len(successes) == 1
    current = (await service.client.get(f"{service.api}/agents/{head['id']}")).json()
    assert current["default_revision_id"] == successes[0].revision.id
    assert (await revision_config(service, current))["instructions"] == successes[0].revision.config.instructions


async def test_builtin_protection_and_workspace_authority_remain_in_force(service):
    model = await create_model(service)
    prepared = await service.client.post(f"{service.api}/agent-composer")
    assert prepared.status_code == 200, prepared.text
    head = prepared.json()
    changed = await edit(service, head, {"instructions": "Allowed preset instructions"})
    with pytest.raises(ServiceError) as protected:
        await edit(service, changed.agent.model_dump(), {"user_questions": False})
    assert protected.value.code == "invalid_argument"
    custom = await create_agent(service, "Restricted", model)
    tenant = service.tenant
    viewer = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["viewer"]),)
    )
    with pytest.raises(ServiceError) as denied:
        await change_configuration(
            service.runtime.storage,
            viewer,
            tenant.workspace_id,
            custom["id"],
            AgentConfigurationChanges.model_validate({"instructions": "Denied"}),
            expected_version=etag(custom),
            registry=service.runtime.registry,
            plugins=service.runtime.plugins,
        )
    assert denied.value.code == "forbidden"


async def test_obsolete_tool_contract_is_readable_but_cannot_be_edited_or_executed(service, monkeypatch):
    from a13n_service.resources.agents import service as agents

    model = await create_model(service)
    head = await create_agent(service, "Historical contract", model)
    original_revision = agents._revision
    stored_toolsets = {
        "agents": {
            "enabled": False,
            "config": {},
            "tools": {"create_agent_revision": {"enabled": False, "permission": "inherit", "config": {}}},
        }
    }

    async def historical_revision(session, current, revision_id):
        row = await original_revision(session, current, revision_id)
        # Emulate a stored pre-cutover immutable revision without changing database history.
        row.config = {**row.config, "toolsets": stored_toolsets}
        view = agents._revision_view(current, row)
        assert view.config.model_dump()["toolsets"] == stored_toolsets
        with pytest.raises(ServiceError) as refused:
            agents._selected(row)
        assert refused.value.details["reason"] == "configuration_unavailable"
        return row

    monkeypatch.setattr(agents, "_revision", historical_revision)
    with pytest.raises(ServiceError) as refused:
        await edit(service, head, {"instructions": "Must not translate old contracts"})
    assert refused.value.code == "conflict"
    assert refused.value.details["reason"] == "configuration_unavailable"
    current = (await service.client.get(f"{service.api}/agents/{head['id']}")).json()
    assert current["version"] == head["version"]


async def test_historical_base_requires_explicit_publication_choice(service):
    head = await create_agent(service, "Explicit base", await create_model(service))
    with pytest.raises(ServiceError) as refused:
        await edit(service, head, {"instructions": "Historical"}, base_revision_id=head["default_revision_id"])
    assert refused.value.code == "invalid_argument"
    assert refused.value.details["field"] == "make_default"
    result = await edit(
        service, head, {"instructions": "Historical"}, base_revision_id=head["default_revision_id"], make_default=False
    )
    assert result.agent.default_revision_id == head["default_revision_id"]


async def test_combined_delta_errors_expose_only_locations_and_types(service):
    head = await create_agent(service, "Safe errors", await create_model(service))
    with pytest.raises(ServiceError) as refused:
        await edit(
            service,
            head,
            {"tool_search": {"groups": {"private-group": {"description": "secret-value", "platform": True}}}},
        )
    assert refused.value.code == "invalid_argument"
    errors = refused.value.details["errors"]
    assert errors and all(set(item) == {"loc", "type"} for item in errors)
    assert "secret-value" not in str(refused.value.details)


@pytest.mark.parametrize(
    "selector",
    [
        "mcp/platform/list_agents",
        "mcp/service-business/list_agents",
        "mcp/service-platform/create_agent_revision",
        "mcp/service-platform/not_a_tool",
        "mcp/service%2Dplatform/list_agents",
    ],
)
def test_reviewer_obsolete_platform_rules_reject_authoring_but_remain_readable(selector):
    from a13n_service.resources.agents import stored

    value = {"model": "model", "reviewer": {"model": "model", "rules": {selector: {"on_flagged": "approval_required"}}}}
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(value)
    assert selector in stored.read(AgentConfig, value).reviewer.rules


def test_reviewer_current_platform_and_connection_rules_are_accepted():
    config = AgentConfig.model_validate(
        {
            "model": "model",
            "connection_tools": [{"connection_id": "conn_aaaaaaaaaaaaaaaaaaaa"}],
            "reviewer": {
                "model": "model",
                "rules": {
                    "mcp/service-platform/list_agents": {},
                    "mcp/service-platform/*": {},
                    "mcp/conn_aaaaaaaaaaaaaaaaaaaa/*": {},
                    "*": {},
                },
            },
        }
    )
    assert len(config.reviewer.rules) == 4
