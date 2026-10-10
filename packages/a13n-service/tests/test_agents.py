"""Agents: configuration normalization, reference validation with field paths, pins, overrides, the build, and
heads' lists, sources and avatars."""

import hashlib
import io
import zipfile
from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

import pytest
from a13n_harness import AbstractHarnessPlugin
from a13n_harness.capabilities import CompactionCapability, SubagentCapability, UserInteractionCapability
from a13n_harness.capabilities.web import WebCapability
from a13n_harness.environment import DynamicEnvironmentCapability
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
    HarnessPluginFactoryRegistration,
)
from a13n_harness.pricing import AbstractModelCostCapability
from a13n_harness.tools.client import ClientToolsCapability
from a13n_harness.tools.permissions import ToolPermissionsCapability
from a13n_service.infra import cursors
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents import composer as builtin_composer
from a13n_service.resources.agents import definition, toolsets
from a13n_service.resources.agents.schemas import (
    AgentConfig,
    AgentOverride,
    AgentValidate,
    OutputSpec,
    RetryConfig,
    SkillSelection,
    SubagentOverride,
    apply_override,
    freeze_override,
)
from a13n_service.resources.agents.service import (
    change_avatar,
    select_revision,
    validate_override,
    validate_revision,
)
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.connections.schemas import ConnectionSelection
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.resources.revisions import resolve_head
from a13n_service.runs.agent import ResolvedAgent, ResolvedSubagent, build, resolve
from a13n_service.settings import Settings
from a13n_service.tenancy.authorize import (
    BUILT_IN_ROLES,
    ExecutionAuthority,
    Grant,
    Principal,
    WorkspaceScope,
    execution_authority,
)
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio
PNG = b"\x89PNG\r\n\x1a\n" + bytes(24)

MODEL, MCP, GITHUB = "gpt", new_object_id("con"), new_object_id("con")
HELPER, HELPER_REVISION, FRESH = new_object_id("ap"), new_object_id("apr"), new_object_id("ap")
SKILL = b"---\nname: code-review\ndescription: Review a change for correctness.\n---\n# Review\n"


def config(**fields: object) -> AgentConfig:
    return AgentConfig.model_validate({"model": MODEL, **fields})


def test_the_toolset_catalogue_names_each_tool_once() -> None:
    catalogue = toolsets.catalog(frozenset({"search"}))
    tools = [tool for toolset in catalogue.items for tool in toolset.tools]

    assert [toolset.key for toolset in catalogue.items] == [
        "files",
        "shell",
        "web",
        "memory",
        "assets",
        "configuration",
    ]
    assert len({tool.execution_id for tool in tools}) == len({tool.model_name for tool in tools}) == len(tools)
    supported = {tool.execution_id: tool.deployment_supported for tool in tools}
    assert supported["web.search"] and not supported["web.scrape"] and supported["web.fetch"]


def test_toolsets_normalize_to_the_whole_catalogue() -> None:
    provider = new_object_id("wp")
    selected = config(toolsets={"web": {"tools": {"search": {"config": {"provider_id": provider}}}}})

    assert set(selected.toolsets) == {"files", "shell", "web", "memory", "assets", "configuration"}
    assert not selected.toolsets["assets"].enabled
    # Memory tools reach a run only through its mounts; a disabled tool is left out of every mount.
    assert toolsets.memory_file_tools(selected.toolsets) == {
        "view",
        "grep",
        "create",
        "edit",
        "append",
        "move",
        "delete",
    }
    reading = config(toolsets={"memory": {"tools": {"file_delete": {"enabled": False}}}})
    assert "delete" not in toolsets.memory_file_tools(reading.toolsets)
    assert toolsets.memory_file_tools(config(toolsets={"memory": {"enabled": False}}).toolsets) == frozenset()
    # Writes of the configuration toolset wait for approval unless the author says otherwise.
    assert selected.toolsets["configuration"].tools["create_agent"].permission == "ask"
    assert selected.toolsets["web"].tools["search"].config == {"provider_id": provider, "max_results": 5}
    web = toolsets.web_tools(selected.toolsets)
    assert web is not None and web.search is not None and web.scrape is None and web.fetch is None
    assert toolsets.web_tools(config().toolsets) is None
    for invalid in (
        {"email": {}},
        {"web": {"tools": {"crawl": {}}}},
        {"web": {"tools": {"search": {"config": {"max_results": 11}}}}},
        {"files": {"config": {"root": "/"}}},
    ):
        with pytest.raises(ValidationError):
            config(toolsets=invalid)


def test_permissions_compile_from_each_selection() -> None:
    selected = config(
        toolsets={"shell": {"tools": {"exec": {"permission": "ask"}}}},
        connection_tools=[
            {"connection_id": MCP, "permission": "review", "permissions": {"drop": "deny"}},
            {
                "connection_id": GITHUB,
                "tools": ["issues", "pulls"],
                "permission": "ask",
                "permissions": {"pulls": "allow"},
            },
        ],
        client_tools=[
            {
                "name": "lookup",
                "description": "Look up",
                "parameters_json_schema": {"type": "object"},
                "permission": "deny",
            }
        ],
    )
    rules = definition.tool_permissions(selected, {MCP: "mcp", GITHUB: "github"}).rules

    assert rules["environment.shell_exec"] == "ask" and rules["filesystem.view"] == "inherit"
    assert "web.search" not in rules
    assert rules[f"mcp/{MCP}/*"] == "review" and rules[f"mcp/{MCP}/drop"] == "deny"
    assert rules[f"tool/{GITHUB}/issues"] == "ask" and rules[f"tool/{GITHUB}/pulls"] == "allow"
    assert rules["tool/a13n-service-client/lookup"] == "deny"
    assert definition.declared_permissions(selected) >= {"ask", "review", "deny", "allow"}
    with pytest.raises(ValidationError):
        ConnectionSelection.model_validate({"connection_id": GITHUB, "tools": ["issues"], "permissions": {"x": "deny"}})


def test_output_schemas_inline_their_references() -> None:
    person = {
        "type": "object",
        "properties": {"name": {"$ref": "#/$defs/name"}},
        "$defs": {"name": {"type": "string"}},
    }
    spec = OutputSpec.model_validate(
        {"name": "person", "schema": {"$ref": "person", "description": "Who"}, "resources": {"person": person}}
    )
    schema = TypeAdapter(definition.output_type(spec)).json_schema()
    assert "$ref" not in str(schema) and schema["type"] == "object"

    variants = OutputSpec.model_validate(
        {"variants": [{"name": "yes", "schema": {"type": "object"}}, {"name": "no", "schema": {"type": "object"}}]}
    )
    assert len(definition.output_type(variants)) == 2
    assert definition.output_type(None) is str

    recursive = {"$ref": "#/$defs/node", "$defs": {"node": {"properties": {"next": {"$ref": "#/$defs/node"}}}}}
    for schema, reason in ((recursive, "the schema is recursive"), ({"$ref": "missing"}, "unresolvable reference")):
        with pytest.raises(ServiceError) as refused:
            definition.output_type(OutputSpec.model_validate({"schema": schema}))
        assert refused.value.details["field"] == "output_spec"
        assert str(refused.value.details["reason"]).startswith(reason)


def test_overrides_replace_or_merge_the_revision_fields() -> None:
    other = new_object_id("ap")
    revision = config(
        model_settings={"temperature": 0.2},
        instructions="Be brief.",
        subagents={
            "helper": {"agent_id": HELPER, "revision_id": HELPER_REVISION, "description": "Helps"},
            "other": {"agent_id": other, "revision_id": new_object_id("apr")},
        },
        retries={"tools": 2},
    )
    override = AgentOverride.model_validate(
        {
            "model_settings": {"max_tokens": 100},
            "instructions": "Be thorough.",
            "toolsets": {"shell": {"enabled": False}},
            "subagents": {"helper": {"description": "Helps more"}, "other": None, "fresh": {"agent_id": FRESH}},
            "retries": {"output": 1},
        }
    )
    applied = apply_override(revision, override)

    assert applied.model == MODEL and applied.model_settings == {"max_tokens": 100}
    assert applied.instructions == "Be thorough." and applied.retries == RetryConfig(tools=2, output=1)
    assert not applied.toolsets["shell"].enabled and applied.toolsets["files"] == revision.toolsets["files"]
    assert set(applied.subagents) == {"helper", "fresh"} and applied.subagents["fresh"].revision_id is None
    helper = applied.subagents["helper"]
    assert (helper.revision_id, helper.description) == (HELPER_REVISION, "Helps more")
    # Another agent on an existing edge runs that agent's default revision, never the old pin.
    moved = apply_override(revision, AgentOverride(subagents={"helper": SubagentOverride(agent_id=FRESH)}))
    assert moved.subagents["helper"].revision_id is None

    pinned = new_object_id("apr")
    validated = applied.model_copy(
        update={
            "subagents": {
                **applied.subagents,
                "fresh": applied.subagents["fresh"].model_copy(update={"revision_id": pinned}),
            }
        }
    )
    frozen = freeze_override(override, validated)
    assert frozen.subagents is not None and frozen.subagents["other"] is None
    assert cast(SubagentOverride, frozen.subagents["fresh"]).revision_id == pinned
    assert cast(SubagentOverride, frozen.subagents["helper"]).revision_id == HELPER_REVISION


@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("choice", [None, False, True])
def test_lazy_environment_override_preserves_false_and_inherits_null(baseline: bool, choice: bool | None) -> None:
    assert config().lazy_environment
    revision = config(lazy_environment=baseline)
    override = AgentOverride(lazy_environment=choice)
    applied = apply_override(revision, override)
    assert applied.lazy_environment is (baseline if choice is None else choice)
    frozen = freeze_override(override, applied)
    restored = AgentOverride.model_validate_json(frozen.model_dump_json())
    assert apply_override(revision, restored).lazy_environment is applied.lazy_environment


class _Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = 1


class _Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class _Factory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "test.audit"

    def validate_configuration(self, configuration: Mapping[str, JsonValue]) -> BaseModel:
        return _Settings.model_validate(configuration)

    def create_plugin(self, context: HarnessPluginFactoryContext) -> AbstractHarnessPlugin:
        return _Plugin(context.plugin_id)


PLUGINS = HarnessPluginFactoryCatalog(
    [(HarnessPluginFactoryRegistration("test.audit", __name__, "_Factory", None, None, None), _Factory())]
)


def resolved(agent_id: str, revision_id: str, selected: AgentConfig, **fields: Any) -> ResolvedAgent:
    model = ResolvedModel(
        id=new_object_id("mdl"),
        key=MODEL,
        version=1,
        config=ModelConfig(model_name="scripted", model_api="openai.chat_completions", temperature=0.5),
        pricing=None,
        provider=cast(Any, None),
    )
    values: dict[str, Any] = {"reviewer": None, "media": {}, "connection_types": {}, "subagents": {}} | fields
    return ResolvedAgent(agent_id=agent_id, revision_id=revision_id, config=selected, model=model, **values)


def test_build_composes_the_definition_without_io() -> None:
    child = resolved(HELPER, HELPER_REVISION, config())
    root_revision = new_object_id("apr")
    selected = config(
        model_settings={"max_tokens": 64},
        instructions="Help.",
        toolsets={"web": {"tools": {"fetch": {}}}, "shell": {"tools": {"exec": {"permission": "review"}}}},
        user_questions=True,
        client_tools=[{"name": "lookup", "description": "Look up", "parameters_json_schema": {"type": "object"}}],
        reviewer={"model": MODEL},
        plugins=[{"instance_name": "audit", "plugin_key": "test.audit"}],
        subagents={"helper": {"agent_id": HELPER, "revision_id": HELPER_REVISION}},
    )
    root = resolved(
        new_object_id("ap"),
        root_revision,
        selected,
        reviewer=child.model,
        subagents={"helper": ResolvedSubagent(selected.subagents["helper"], child.revision_id, "Helps", child)},
    )
    asked: list[str] = []

    def capabilities(agent: ResolvedAgent) -> list[Any]:
        asked.append(agent.revision_id)
        return []

    built = build(root, capabilities=capabilities, plugins=PLUGINS, instrumentation=None)
    composed = built.definition

    # Each agent selects its model by key, and its calls are priced by the model they select.
    [prices] = [item for item in composed.capabilities if isinstance(item, AbstractModelCostCapability)]
    assert {type(capability) for capability in composed.capabilities} - {type(prices)} == {
        DynamicEnvironmentCapability,
        ToolPermissionsCapability,
        CompactionCapability,
        WebCapability,
        UserInteractionCapability,
        ClientToolsCapability,
        SubagentCapability,
    }
    assert asked == [root_revision, HELPER_REVISION]
    assert composed.definition_id == root_revision and composed.agent.instructions == "Help."
    assert composed.agent.model_settings == {"temperature": 0.5, "max_tokens": 64}
    assert [plugin.plugin_id for plugin in composed.plugins] == ["audit"]
    (subagent,) = composed.subagents
    assert (subagent.name, subagent.description, subagent.agent.definition_id) == ("helper", "Helps", HELPER_REVISION)
    assert composed.model is None and composed.agent.model == subagent.agent.agent.model == MODEL


async def create_model(service, **config: object) -> str:  # type: ignore[no-untyped-def]
    """A new model's key."""
    provider = await service.client.post(
        f"{service.api}/model-providers",
        json={"type": "openai", "name": "Provider", "credential": {"api_key": "sk-test"}},
    )
    assert provider.status_code == 201, provider.text
    model = await service.client.post(
        f"{service.api}/models",
        json={
            "provider_id": provider.json()["id"],
            "key": f"model-{uuid4().hex[:12]}",
            "name": "Model",
            "config": {"model_name": "scripted", "model_api": "openai.chat_completions", **config},
        },
    )
    assert model.status_code == 201, model.text
    return model.json()["key"]


async def create_agent(service, name: str, model: str, **config: object) -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.post(
        f"{service.api}/agents", json={"name": name.title(), "config": {"model": model, **config}}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def revision_config(service, agent: dict) -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.get(f"{service.api}/agents/{agent['id']}/revisions/{agent['default_revision_id']}")
    assert response.status_code == 200, response.text
    return response.json()["config"]


def etag(resource: dict) -> str:
    """A model's ETag names its key, every other resource's its ID."""
    return f'"{resource["id"] if "id" in resource else resource["key"]}:{resource["version"]}"'


async def create_skill(service) -> dict:  # type: ignore[no-untyped-def]
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as package:
        package.writestr("SKILL.md", SKILL)
    upload = await service.client.post(
        f"{service.api}/uploads",
        files={"file": ("skill.zip", data.getvalue(), "application/zip")},
        headers={"idempotency-key": uuid4().hex},
    )
    assert upload.status_code == 200, upload.text
    skill = await service.client.post(
        f"{service.api}/skills", json={"source": {"kind": "upload", "upload_id": upload.json()["upload_id"]}}
    )
    assert skill.status_code == 201, skill.text
    return skill.json()


async def test_an_agents_skills_declare_distinct_names(service) -> None:  # type: ignore[no-untyped-def]
    """The model sees each skill by its SKILL.md name: skills of a workspace may share one, those of an agent not."""
    model = await create_model(service)
    first, second = await create_skill(service), await create_skill(service)
    both = [{"skill_id": first["id"]}, {"skill_id": second["id"]}]
    refused = await service.client.post(
        f"{service.api}/agents", json={"name": "Both", "config": {"model": model, "skills": both}}
    )
    assert refused.status_code == 400 and refused.json()["error"]["details"]["field"] == "skills.1", refused.text
    assert (await create_agent(service, "second", model, skills=both[1:]))["name"] == "Second"


async def test_references_are_checked_at_their_field_path(service) -> None:  # type: ignore[no-untyped-def]
    model = await create_model(service)
    child = await create_agent(service, "child", model)
    missing = {kind: new_object_id(kind) for kind in ("ap", "con", "wp", "et")}
    dedicated = {"mode": "dedicated", "template_id": missing["et"]}
    brave = await service.client.post(
        f"{service.api}/web-providers",
        json={"type": "brave", "name": "Brave", "credential": {"api_key": "brave"}},
    )
    search_only = {"toolsets": {"web": {"tools": {"scrape": {"config": {"provider_id": brave.json()["id"]}}}}}}
    cases: list[tuple[dict[str, object], str]] = [
        ({"model": "missing"}, "model"),
        ({"reviewer": {"model": "missing"}}, "reviewer.model"),
        ({"model_settings": {"temperature": "warm"}}, "model_settings.temperature"),
        # Raw inference cannot change upstream selection, and timeouts remain operator-owned.
        ({"model_settings": {"extra_body": {"model": "other"}}}, "model_settings.extra_body"),
        ({"model_settings": {"timeout": 30}}, "model_settings"),
        ({"reviewer": {"model": model, "model_settings": {"unknown": 1}}}, "reviewer.model_settings"),
        ({"media_understanding": {"image": model}}, "media_understanding.image"),
        ({"skills": [{"skill_id": new_object_id("sk")}]}, "skills.0"),
        ({"subagents": {"helper": {"agent_id": missing["ap"]}}}, "subagents.helper"),
        ({"connection_tools": [{"connection_id": missing["con"]}]}, "connection_tools.0"),
        ({"toolsets": {"web": {"tools": {"search": {}}}}}, "toolsets.web.tools.search.config.provider_id"),
        (
            {"toolsets": {"web": {"tools": {"scrape": {"config": {"provider_id": missing["wp"]}}}}}},
            "toolsets.web.tools.scrape.config.provider_id",
        ),
        ({"toolsets": {"shell": {"tools": {"exec": {"permission": "review"}}}}}, "reviewer"),
        (
            {"client_tools": [{"name": "view", "description": "View", "parameters_json_schema": {"type": "object"}}]},
            "client_tools.0.name",
        ),
        ({"plugins": [{"instance_name": "audit", "plugin_key": "test.audit"}]}, "plugins.0"),
        ({"default_environment_template_id": missing["et"]}, "default_environment_template_id"),
        (
            {"subagents": {"helper": {"agent_id": child["id"], "environment": dedicated}}},
            "subagents.helper.environment.template_id",
        ),
        ({"output_spec": {"schema": {"$ref": "missing"}}}, "output_spec"),
    ]
    for changes, field in cases:
        response = await service.client.post(
            f"{service.api}/agents", json={"name": "Invalid", "config": {"model": model, **changes}}
        )
        assert response.status_code == 400, (field, response.text)
        assert response.json()["error"]["details"]["field"] == field, response.text

    # A provider whose type does not serve the operation is refused here, never at run time.
    response = await service.client.post(
        f"{service.api}/agents", json={"name": "Invalid", "config": {"model": model, **search_only}}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"] == {
        "field": "toolsets.web.tools.scrape.config.provider_id",
        "reason": "brave does not support scrape",
    }


async def test_settings_of_a_model_api_no_longer_offered_are_unavailable(service, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A distribution that drops a calling API makes the settings of its models unavailable, as a dropped type is."""
    model = await create_model(service)
    monkeypatch.delitem(service.runtime.registry.model_settings, "openai.chat_completions")
    response = await service.client.post(f"{service.api}/agents", json={"name": "Dropped", "config": {"model": model}})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"] == {"dependency": "model_api:openai.chat_completions"}


async def test_a_locked_head_is_read_fresh(service) -> None:  # type: ignore[no-untyped-def]
    """Locking a head this session already read refreshes that copy, so an ETag is checked against the current
    version."""
    agent = await create_agent(service, "fresh", await create_model(service))
    storage, workspace_id = service.runtime.storage, service.tenant.workspace_id
    async with transaction(storage) as session:
        stale = await resolve_head(session, AgentRow, workspace_id, agent["id"])
        async with transaction(storage) as other:
            await other.execute(update(AgentRow).where(AgentRow.id == agent["id"]).values(name="Renamed"))
        locked = await resolve_head(session, AgentRow, workspace_id, agent["id"], lock=True)
        assert locked is stale and (locked.name, locked.version) == ("Renamed", agent["version"] + 1)


async def test_revisions_pin_skills_and_subagents(service) -> None:  # type: ignore[no-untyped-def]
    model = await create_model(service)
    skill = await create_skill(service)
    child = await create_agent(service, "child", model)
    parent = await create_agent(
        service, "parent", model, skills=[{"skill_id": skill["id"]}], subagents={"helper": {"agent_id": child["id"]}}
    )

    pinned = await revision_config(service, parent)
    assert pinned["skills"] == [{"skill_id": skill["id"], "revision_id": skill["default_revision_id"]}]
    assert pinned["subagents"]["helper"]["revision_id"] == child["default_revision_id"]

    # An inline path back to the agent itself is refused; an async child starts a child run of its own.
    revisions = f"{service.api}/agents/{child['id']}/revisions"
    back = {"model": model, "subagents": {"back": {"agent_id": parent["id"]}}}
    cycle = await service.client.post(revisions, json={"config": back}, headers={"if-match": etag(child)})
    assert cycle.status_code == 400, cycle.text
    assert cycle.json()["error"]["details"] == {
        "field": "subagents.back",
        "reason": "the subagents lead back to this agent",
    }
    delegated = await service.client.post(
        revisions, json={"config": {**back, "subagent_mode": "async"}}, headers={"if-match": etag(child)}
    )
    assert delegated.status_code == 201, delegated.text

    # A child run carries only its edge's request limit; token and tool-call limits bound inline delegation.
    current = {"if-match": (await service.client.get(f"{service.api}/agents/{child['id']}")).headers["etag"]}
    limited = {"back": {"agent_id": parent["id"], "usage_limits": {"request_limit": 3, "total_tokens_limit": 1000}}}
    async_config = {**back, "subagent_mode": "async", "subagents": limited}
    refused = await service.client.post(revisions, json={"config": async_config}, headers=current)
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["details"] == {
        "field": "subagents.back.usage_limits",
        "reason": "an async child run takes only request_limit",
    }
    requests_only = {"back": {"agent_id": parent["id"], "usage_limits": {"request_limit": 3}}}
    bounded = await service.client.post(
        revisions, json={"config": {**async_config, "subagents": requests_only}}, headers=current
    )
    assert bounded.status_code == 201, bounded.text
    inline = {"model": model, "subagents": {"helper": limited["back"] | {"agent_id": child["id"]}}}
    assert (await service.client.post(f"{service.api}/agents/validate", json={"config": inline})).status_code == 204


def viewer(service) -> Principal:  # type: ignore[no-untyped-def]
    tenant = service.tenant
    grant = Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["viewer"])
    return Principal(tenant.principal_id, "user", (grant,))


async def test_configurations_validate_as_revision_creation_would_without_storing(service) -> None:  # type: ignore[no-untyped-def]
    model = await create_model(service)
    child = await create_agent(service, "child", model)
    parent = await create_agent(service, "parent", model, subagents={"helper": {"agent_id": child["id"]}})
    validate = f"{service.api}/agents/validate"
    valid = {"model": model, "subagents": {"helper": {"agent_id": child["id"]}}}
    checked = await service.client.post(validate, json={"config": valid})
    assert checked.status_code == 204, checked.text

    for changes, field in (
        ({"model": "missing-model"}, "model"),
        ({"skills": [{"skill_id": new_object_id("sk")}]}, "skills.0"),
        ({"toolsets": {"shell": {"tools": {"exec": {"permission": "review"}}}}}, "reviewer"),
    ):
        refused = await service.client.post(validate, json={"config": {"model": model, **changes}})
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["details"]["field"] == field

    # Naming the agent applies the rule that its inline subagents may not lead back to it, as its revisions do.
    back = {"model": model, "subagents": {"back": {"agent_id": parent["id"]}}}
    assert (await service.client.post(validate, json={"config": back})).status_code == 204
    cycle = await service.client.post(validate, json={"config": back, "agent_id": child["id"]})
    revision = await service.client.post(
        f"{service.api}/agents/{child['id']}/revisions", json={"config": back}, headers={"if-match": etag(child)}
    )
    assert cycle.status_code == revision.status_code == 400
    assert (
        cycle.json()["error"]["details"]
        == revision.json()["error"]["details"]
        == {
            "field": "subagents.back",
            "reason": "the subagents lead back to this agent",
        }
    )
    unknown = await service.client.post(validate, json={"config": back, "agent_id": new_object_id("ap")})
    assert unknown.status_code == 404, unknown.text

    # Nothing was stored, and only authors may validate.
    listed = (await service.client.get(f"{service.api}/agents")).json()["items"]
    assert sorted((agent["name"], agent["version"]) for agent in listed) == [
        ("Child", child["version"]),
        ("Parent", parent["version"]),
    ]
    runtime = service.runtime
    with pytest.raises(ServiceError) as denied:
        await validate_revision(
            runtime.storage,
            viewer(service),
            service.tenant.workspace_id,
            AgentValidate.model_validate({"config": valid}),
            registry=runtime.registry,
            plugins=runtime.plugins,
        )
    assert denied.value.code == "forbidden"


async def test_agents_use_ids_and_lists_filter(service) -> None:  # type: ignore[no-untyped-def]
    model = await create_model(service)
    writer = await create_agent(service, "writer", model)
    reader = await create_agent(service, "reader", model)
    agents = f"{service.api}/agents"
    assert "key" not in writer and writer["source"] == "custom"
    renamed = await service.client.patch(
        f"{agents}/{writer['id']}", json={"name": "Author"}, headers={"if-match": etag(writer)}
    )
    assert renamed.status_code == 200 and renamed.json()["id"] == writer["id"], renamed.text
    assert (await service.client.get(f"{agents}/writer")).status_code == 404
    async with short_session(service.runtime.storage) as session:
        events = (
            await session.scalars(
                select(AuditEventRow.details).where(
                    AuditEventRow.action == "agent.update", AuditEventRow.target_id == writer["id"]
                )
            )
        ).all()
    assert events == [{"fields": ["name"]}]
    composer = await service.client.post(f"{service.api}/agent-composer")
    assert composer.status_code == 200 and composer.json()["source"] == "builtin", composer.text
    builtin = await service.client.patch(
        f"{agents}/{composer.json()['id']}", json={"name": "Helper"}, headers={"if-match": composer.headers["etag"]}
    )
    assert builtin.status_code == 409 and builtin.json()["error"]["details"]["reason"] == "builtin"

    async def ids(**params: str) -> set[str]:
        response = await service.client.get(agents, params=params)
        assert response.status_code == 200, response.text
        return {agent["id"] for agent in response.json()["items"]}

    await service.client.post(f"{agents}/{reader['id']}/archive", headers={"if-match": etag(reader)})
    assert await ids(q="AUTH") == {writer["id"]}
    assert await ids(q="composer") == {composer.json()["id"]}
    assert await ids(source="builtin") == {composer.json()["id"]}
    assert await ids(source="custom", archived="false") == {writer["id"]}
    assert await ids(archived="true") == {reader["id"]}
    assert await ids(archived="false") == {writer["id"], composer.json()["id"]}
    assert await ids(q="read", archived="false") == set()
    assert (await service.client.get(agents, params={"q": ""})).status_code == 400


async def test_preparing_composer_refreshes_metadata_without_a_revision(service, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await create_model(service)
    prepare = f"{service.api}/agent-composer"
    with monkeypatch.context() as previous:
        previous.setattr(builtin_composer, "NAME", "Previous name")
        previous.setattr(builtin_composer, "DESCRIPTION", "Previous description")
        initial = await service.client.post(prepare)
    assert initial.status_code == 200, initial.text
    before = initial.json()

    refreshed = await service.client.post(prepare)
    assert refreshed.status_code == 200, refreshed.text
    composer = refreshed.json()
    assert (composer["name"], composer["description"]) == ("Agent Composer", "Create and refine your agents.")
    assert composer["id"] == before["id"]
    assert composer["default_revision_id"] == before["default_revision_id"]
    assert composer["version"] == before["version"] + 1
    assert (await service.client.get(f"{service.api}/agents/{composer['id']}")).json() == composer
    repeated = await service.client.post(prepare)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == composer
    async with short_session(service.runtime.storage) as session:
        events = (
            await session.scalars(
                select(AuditEventRow.details).where(
                    AuditEventRow.action == "agent.update", AuditEventRow.target_id == composer["id"]
                )
            )
        ).all()
    assert events == [{"fields": ["name", "description"]}]


async def test_agent_avatars(service, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    model = await create_model(service)
    agent = await create_agent(service, "painter", model)
    avatar = f"{service.api}/agents/{agent['id']}/avatar"

    def changing(item: dict) -> dict[str, str]:
        return {"if-match": etag(item), "content-type": "image/png"}

    assert agent["image_url"] is None
    assert (await service.client.put(avatar, content=PNG, headers={"content-type": "image/png"})).status_code == 428
    svg = await service.client.put(
        avatar, content=b"<svg xmlns='http://www.w3.org/2000/svg'/>", headers=changing(agent)
    )
    assert svg.status_code == 400 and svg.json()["error"]["details"]["reason"] == "unsupported_type"
    stored = await service.client.put(avatar, content=PNG, headers=changing(agent))
    assert stored.status_code == 200, stored.text
    digest = hashlib.sha256(PNG).hexdigest()
    assert stored.json()["image_url"] == f"{avatar}?v={digest}"
    assert stored.headers["etag"] == etag(stored.json()) and stored.json()["version"] > agent["version"]
    objects = settings.objects.root / f"orgs/{service.tenant.organization_id}/images/{agent['id']}"
    assert [path.read_bytes() for path in objects.iterdir()] == [PNG]
    served = await service.client.get(stored.json()["image_url"])
    assert (served.status_code, served.content, served.headers["content-type"]) == (200, PNG, "image/png")
    assert served.headers["x-content-type-options"] == "nosniff"
    [listed] = (await service.client.get(f"{service.api}/agents")).json()["items"]
    assert listed["image_url"] == stored.json()["image_url"]
    assert (await service.client.put(avatar, content=PNG, headers=changing(agent))).status_code == 412

    # Only authors change avatars of open agents, and a refused change stores nothing.
    jpeg = b"\xff\xd8\xff\xe0" + bytes(24)
    runtime = service.runtime
    with pytest.raises(ServiceError) as denied:
        await change_avatar(
            runtime.storage,
            runtime.objects,
            viewer(service),
            service.tenant.workspace_id,
            agent["id"],
            jpeg,
            if_match=etag(stored.json()),
        )
    assert denied.value.code == "forbidden"
    item = f"{service.api}/agents/{agent['id']}"
    archived = (await service.client.post(f"{item}/archive", headers={"if-match": etag(stored.json())})).json()
    closed = await service.client.put(avatar, content=jpeg, headers=changing(archived))
    assert closed.status_code == 409 and closed.json()["error"]["details"]["reason"] == "archived"
    assert [path.read_bytes() for path in objects.iterdir()] == [PNG]
    restored = (await service.client.post(f"{item}/unarchive", headers={"if-match": etag(archived)})).json()
    removed = await service.client.delete(avatar, headers={"if-match": etag(restored)})
    assert removed.status_code == 200 and removed.json()["image_url"] is None
    assert (await service.client.get(avatar)).status_code == 404


async def test_heads_duplicate_archive_and_offer_toolsets(service) -> None:  # type: ignore[no-untyped-def]
    model = await create_model(service)
    agent = await create_agent(service, "source", model, instructions="Be brief.")
    base = f"{service.api}/agents/{agent['id']}"

    copy = await service.client.post(f"{base}/duplicate", json={"name": "Copy"})
    assert copy.status_code == 201, copy.text
    assert copy.json()["id"] != agent["id"]
    assert await revision_config(service, copy.json()) == await revision_config(service, agent)

    labelled = {"name": "Renamed", "labels": {"team": "tools"}}
    renamed = await service.client.patch(base, json=labelled, headers={"if-match": etag(agent)})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed", renamed.text
    agents = f"{service.api}/agents"
    tools = (await service.client.get(agents, params={"label": "team:tools"})).json()["items"]
    assert [item["id"] for item in tools] == [agent["id"]]
    assert (await service.client.get(agents, params={"label": "team:platform"})).json()["items"] == []
    # A cursor continues only the query that issued it.
    first = (await service.client.get(agents, params={"limit": 1})).json()
    rest = await service.client.get(agents, params={"limit": 1, "cursor": first["next_cursor"]})
    assert rest.status_code == 200 and rest.json()["items"] != first["items"], rest.text
    filtered = await service.client.get(agents, params={"limit": 1, "cursor": first["next_cursor"], "q": "Copy"})
    assert (filtered.status_code, filtered.json()["error"]["code"]) == (400, "invalid_cursor")
    # A position past any revision number is refused before it reaches the database.
    beyond = cursors.encode("agent_revisions", agent["id"], 2**31)
    refused = await service.client.get(f"{base}/revisions", params={"cursor": beyond})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_cursor"

    archived = await service.client.post(f"{base}/archive", headers={"if-match": etag(renamed.json())})
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None, archived.text
    # An archived agent changes only by unarchiving.
    closed = await service.client.patch(base, json={"name": "Closed"}, headers={"if-match": etag(archived.json())})
    assert closed.status_code == 409 and closed.json()["error"]["details"]["reason"] == "archived", closed.text
    refused = await service.client.post(f"{base}/duplicate", json={"name": "Again"})
    assert refused.status_code == 409, refused.text
    revision = await service.client.post(
        f"{base}/revisions",
        json={"config": {"model": model}},
        headers={"if-match": etag(archived.json())},
    )
    assert revision.status_code == 409, revision.text
    restored = await service.client.post(f"{base}/unarchive", headers={"if-match": etag(archived.json())})
    assert restored.status_code == 200 and restored.json()["archived_at"] is None, restored.text

    catalogue = await service.client.get(f"{service.api}/toolsets")
    assert catalogue.status_code == 200, catalogue.text
    assert [toolset["key"] for toolset in catalogue.json()["items"]] == [
        "files",
        "shell",
        "web",
        "memory",
        "assets",
        "configuration",
    ]


async def test_overrides_are_validated_and_pinned_as_the_run_freezes_them(service) -> None:  # type: ignore[no-untyped-def]
    model = await create_model(service)
    child = await create_agent(service, "child", model)
    agent = await create_agent(service, "parent", model)
    tenant = service.tenant
    principal = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    scope = WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    authority = ExecutionAuthority(
        principal_id=tenant.principal_id,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        verbs=BUILT_IN_ROLES["admin"],
    )

    async def validate(override: AgentOverride, agent_id: str = agent["id"]) -> AgentOverride:
        async with short_session(service.runtime.storage) as session:
            revision = await select_revision(session, tenant.workspace_id, agent_id, None)
            return await validate_override(
                session,
                principal,
                scope,
                revision,
                override,
                authority=authority,
                registry=service.runtime.registry,
                plugins=service.runtime.plugins,
            )

    frozen = await validate(AgentOverride(subagents={"extra": SubagentOverride(agent_id=child["id"])}))
    assert frozen.subagents == {
        "extra": SubagentOverride(agent_id=child["id"], revision_id=child["default_revision_id"])
    }
    for override, field in (
        (AgentOverride(model="missing-model"), "model"),
        (AgentOverride(subagents={"extra": SubagentOverride(description="No agent")}), "subagents.extra.agent_id"),
    ):
        with pytest.raises(ServiceError) as refused:
            await validate(override)
        assert refused.value.code == "invalid_argument" and refused.value.details["field"] == field

    # A run keeps the pins its revision holds, as a plain run does, even once their skill or agent is archived;
    # only a pin the override adds must name an unarchived one.
    skill = await create_skill(service)
    pinning = await create_agent(
        service,
        "pinning",
        model,
        skills=[{"skill_id": skill["id"]}],
        subagents={"helper": {"agent_id": child["id"]}},
    )
    for path, head in ((f"skills/{skill['id']}", skill), (f"agents/{child['id']}", child)):
        archived = await service.client.post(f"{service.api}/{path}/archive", headers={"if-match": etag(head)})
        assert archived.status_code == 200, archived.text
    kept = await validate(AgentOverride(instructions="Only this run."), pinning["id"])
    assert kept.instructions == "Only this run."
    for override, field in (
        (AgentOverride(subagents={"extra": SubagentOverride(agent_id=child["id"])}), "subagents.extra"),
        (AgentOverride(skills=(SkillSelection(skill_id=skill["id"]),)), "skills.0"),
    ):
        with pytest.raises(ServiceError) as archived_pin:
            await validate(override)
        assert (archived_pin.value.details["field"], archived_pin.value.details["reason"]) == (field, "archived")


async def test_publishing_the_default_configuration_again_changes_nothing(service) -> None:  # type: ignore[no-untyped-def]
    """A revision whose configuration equals the default revision's is that revision: no new number, ETag or
    audit event, whatever `make_default` says."""
    model = await create_model(service)
    agent = await create_agent(service, "steady", model, instructions="Be brief.")
    item = f"{service.api}/agents/{agent['id']}"
    same = {"model": model, "instructions": "Be brief."}
    for make_default in (True, False):
        again = await service.client.post(
            f"{item}/revisions", json={"config": same, "make_default": make_default}, headers={"if-match": etag(agent)}
        )
        assert again.status_code == 201, again.text
        assert (again.json()["id"], again.json()["number"]) == (agent["default_revision_id"], 1)
    assert (await service.client.get(item)).json()["version"] == agent["version"]

    # Another configuration without `make_default` is kept beside the default.
    other = {**same, "instructions": "Be thorough."}
    kept = await service.client.post(
        f"{item}/revisions", json={"config": other, "make_default": False}, headers={"if-match": etag(agent)}
    )
    assert kept.status_code == 201 and kept.json()["number"] == 2, kept.text
    head = (await service.client.get(item)).json()
    assert head["default_revision_id"] == agent["default_revision_id"] and head["version"] > agent["version"]
    async with short_session(service.runtime.storage) as session:
        events = (
            await session.execute(
                select(AuditEventRow.action, AuditEventRow.details)
                .where(AuditEventRow.target_id == agent["id"])
                .order_by(AuditEventRow.occurred_at)
            )
        ).all()
    assert [tuple(event) for event in events] == [
        ("agent.create", {}),
        ("agent.revision.create", {"revision_id": kept.json()["id"]}),
    ]


async def test_workspace_media_defaults_fill_what_an_agent_leaves_unselected(service) -> None:  # type: ignore[no-untyped-def]
    reader = await create_model(service, characteristics={"capabilities": ["image_understanding"]})
    plain = await create_model(service)
    agent = await create_agent(service, "viewer", plain)
    defaults = f"{service.api}/media-understanding-defaults"
    current = await service.client.get(defaults)
    assert current.status_code == 200 and current.json()["image"] is None, current.text
    defaults_etag = current.headers["etag"]

    assert (await service.client.put(defaults, json={"image": reader})).status_code == 428
    refused = await service.client.put(defaults, json={"image": plain}, headers={"if-match": defaults_etag})
    assert refused.status_code == 400 and refused.json()["error"]["details"]["field"] == "image", refused.text
    replaced = await service.client.put(defaults, json={"image": reader}, headers={"if-match": defaults_etag})
    assert replaced.status_code == 200 and replaced.json()["image"] == reader, replaced.text
    # Replacing the defaults with themselves changes nothing and records nothing.
    again = await service.client.put(defaults, json={"image": reader}, headers={"if-match": replaced.headers["etag"]})
    assert again.json()["version"] == replaced.json()["version"]
    async with short_session(service.runtime.storage) as session:
        events = (
            await session.scalars(select(AuditEventRow).where(AuditEventRow.action == "workspace.media.replace"))
        ).all()
    assert [(event.target_id, event.outcome, event.details) for event in events] == [
        (service.tenant.workspace_id, "ok", {"media": {"image": reader}})
    ]

    tenant = service.tenant
    principal = Principal(tenant.principal_id, "user", (Grant(tenant.organization_id, None, BUILT_IN_ROLES["admin"]),))
    scope = WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    authority = execution_authority(principal, scope)

    async def media() -> dict[str, str]:
        async with short_session(service.runtime.storage) as session:
            revision = await select_revision(session, tenant.workspace_id, agent["id"], None)
            resolved = await resolve(
                session,
                principal,
                scope,
                revision,
                authority=authority,
                override=None,
                registry=service.runtime.registry,
            )
        return {kind: model.key for kind, model in resolved.media.items()}

    assert await media() == {"image": reader}
    model = (await service.client.get(f"{service.api}/models/{reader}")).json()
    disabled = await service.client.patch(
        f"{service.api}/models/{reader}",
        json={"enabled": False},
        headers={"if-match": etag(model)},
    )
    assert disabled.status_code == 200, disabled.text
    # A default that stopped working leaves the kind unavailable instead of failing the agent's runs.
    assert await media() == {}
