"""Models: creation and updates, scope compatibility with the provider, resolution and execution."""

from decimal import Decimal

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.models.runtime import open_model
from a13n_service.resources.models.schemas import ModelConfig, ModelCreate, ModelUpdate
from a13n_service.resources.models.service import create_model, resolve_model, update_model
from a13n_service.runs.tables import UsageRecordRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, ExecutionAuthority, Grant, Principal, WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic_ai.models.openai import OpenAIResponsesModel
from sqlalchemy import select

pytestmark = pytest.mark.anyio

SECRET = "sk-model-secret"


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


async def post(service, path: str, body: dict, status: int = 201) -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.post(service.organization + path, json=body)
    assert response.status_code == status, response.text
    return response.json()


async def provider(service, workspace_id: str | None = None, **changes: object) -> dict:  # type: ignore[no-untyped-def]
    body = {"workspace_id": workspace_id, "type": "openai", "name": "OpenAI", "credential": {"api_key": SECRET}}
    return await post(service, "/model-providers", {**body, **changes})


def manual(provider_id: str, key: str, workspace_id: str | None, **config: object) -> dict:
    return {
        "workspace_id": workspace_id,
        "provider_id": provider_id,
        "key": key,
        "name": key,
        "config": {"model_name": key, "model_api": "openai.chat_completions", **config},
    }


def price(model: str, provider: str = "openai", input_mtok: str = "5") -> dict:
    prices = [{"price_key": "input_mtok", "price": input_mtok}, {"price_key": "output_mtok", "price": "30"}]
    return {
        "provider": provider,
        "model": model,
        "rules": [{"rule_id": "standard", "prices": prices}],
        "source": "models.dev",
        "source_revision": "2026-05-01",
    }


async def add_workspace(service) -> str:  # type: ignore[no-untyped-def]
    workspace_id = new_object_id("ws")
    async with transaction(service.runtime.storage) as session:
        session.add(
            WorkspaceRow(id=workspace_id, organization_id=service.tenant.organization_id, key="second", name="Second")
        )
    return workspace_id


def admin(service) -> Principal:  # type: ignore[no-untyped-def]
    return Principal(
        service.tenant.principal_id, "user", (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES["admin"]),)
    )


async def test_models_are_created_with_their_configuration_and_the_catalog_model_they_started_from(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    body = {
        **manual(
            shared["id"], "gpt-5-5", None, model_name="gpt-5.5", characteristics={"context_window_tokens": 1050000}
        ),
        "pricing": price("gpt-5.5"),
        "catalog_ref": {"provider": "openai", "model": "gpt-5.5"},
    }
    created = await post(service, "/models", body)
    assert created["config"]["model_name"] == "gpt-5.5"
    assert created["config"]["characteristics"]["context_window_tokens"] == 1050000
    assert created["pricing"]["model"] == "gpt-5.5"
    assert created["catalog_ref"] == {"provider": "openai", "model": "gpt-5.5"}

    duplicate = await post(service, "/models", body, status=409)
    assert duplicate["error"]["code"] == "already_exists"
    # The reference is provenance, checked for shape only: the catalog may list the model no more, or never have.
    unlisted = {**body, "key": "gateway", "catalog_ref": {"provider": "openrouter", "model": "vendor/unlisted"}}
    gateway = await post(service, "/models", unlisted)
    assert gateway["catalog_ref"] == unlisted["catalog_ref"]
    await post(
        service, "/models", {**body, "key": "bad-ref", "catalog_ref": {"provider": "Open AI", "model": "x"}}, 400
    )
    await post(service, "/models", {**body, "key": "catalog-key", "catalog_key": "openai:gpt-5.5"}, status=400)
    await post(service, "/models", {key: value for key, value in body.items() if key != "config"}, status=400)

    workspace_model = await post(service, "/models", manual(shared["id"], "custom", service.tenant.workspace_id))
    assert workspace_model["workspace_id"] == service.tenant.workspace_id
    assert workspace_model["pricing"] is None and workspace_model["catalog_ref"] is None
    unsupported = await post(
        service, "/models", manual(shared["id"], "messages", None, model_api="anthropic.messages"), status=400
    )
    assert unsupported["error"]["details"]["field"] == "config.model_api"

    listed = (await service.client.get(f"{service.organization}/models")).json()
    assert {item["id"] for item in listed["items"]} == {created["id"], gateway["id"], workspace_model["id"]}


async def test_a_model_scope_must_be_covered_by_its_provider(service) -> None:  # type: ignore[no-untyped-def]
    first, second = service.tenant.workspace_id, await add_workspace(service)
    confined = await provider(service, first)
    for workspace_id in (None, second):
        refused = await post(service, "/models", manual(confined["id"], "model", workspace_id), status=400)
        assert refused["error"]["details"]["field"] == "workspace_id"
    await post(service, "/models", manual(confined["id"], "model", first))

    disabled = await provider(service, None, name="Disabled")
    patched = await service.client.patch(
        f"{service.organization}/model-providers/{disabled['id']}",
        json={"enabled": False},
        headers={"if-match": etag(disabled)},
    )
    assert patched.status_code == 200, patched.text
    refused = await post(service, "/models", manual(disabled["id"], "model", None), status=422)
    assert refused["error"]["details"] == {"kind": "model_provider", "id": disabled["id"]}

    # A model spends its provider's credential, so a workspace builder configures models only under providers
    # it may write: never in the shared collection, nor under a shared provider, even for its own workspace.
    storage, organization_id, registry = (
        service.runtime.storage,
        service.tenant.organization_id,
        service.runtime.registry,
    )
    builder = Principal(
        service.tenant.principal_id, "user", (Grant(organization_id, first, BUILT_IN_ROLES["builder"]),)
    )
    shared = await provider(service, None, name="Shared")
    for workspace_id in (None, first):
        with pytest.raises(ServiceError, match="cannot perform"):
            body = ModelCreate.model_validate(manual(shared["id"], "model", workspace_id))
            await create_model(storage, builder, organization_id, body, registry=registry)
    body = ModelCreate.model_validate(manual(confined["id"], "own", first))
    assert (await create_model(storage, builder, organization_id, body, registry=registry)).workspace_id == first

    # Under a shared provider, the workspace's model may be renamed there, but only reconfigured by the provider's writers.
    used = await post(service, "/models", manual(shared["id"], "used", first))
    renamed = await update_model(
        storage,
        builder,
        organization_id,
        used["id"],
        ModelUpdate(name="Renamed"),
        if_match=etag(used),
        registry=registry,
    )
    config = ModelConfig.model_validate({**used["config"], "max_tokens": 1024})
    with pytest.raises(ServiceError, match="cannot perform"):
        await update_model(
            storage,
            builder,
            organization_id,
            used["id"],
            ModelUpdate(config=config),
            if_match=f'"{renamed.id}:{renamed.version}"',
            registry=registry,
        )


async def test_models_resolve_for_execution_only_while_enabled_and_in_scope(service) -> None:  # type: ignore[no-untyped-def]
    organization_id, workspace_id = service.tenant.organization_id, service.tenant.workspace_id
    scope = WorkspaceScope(organization_id, workspace_id)
    shared = await provider(service)
    model = await post(service, "/models", manual(shared["id"], "gpt", None, max_tokens=1024))
    item = f"{service.organization}/models/{model['id']}"

    assert (await service.client.patch(item, json={"enabled": False})).status_code == 428
    disabled = await service.client.patch(
        item, json={"enabled": False, "name": "Paused"}, headers={"if-match": etag(model)}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False and disabled.json()["name"] == "Paused"
    storage, actor = service.runtime.storage, admin(service)
    async with short_session(storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, actor, scope, model["id"])
    assert refused.value.code == "disabled"

    enabled = await service.client.patch(item, json={"enabled": True}, headers={"if-match": disabled.headers["etag"]})
    assert enabled.status_code == 200, enabled.text
    async with short_session(storage) as session:
        resolved = await resolve_model(session, actor, scope, model["id"])
    assert resolved.config.max_tokens == 1024 and resolved.provider.id == shared["id"]
    assert resolved.provider.reveal_credential(service.runtime.keys) == {"api_key": SECRET}
    assert SECRET not in repr(resolved)

    # Execution authority narrows what the run's principal may use.
    reader = ExecutionAuthority(
        principal_id=actor.id, organization_id=organization_id, workspace_id=workspace_id, verbs=frozenset({"read"})
    )
    elsewhere = WorkspaceScope(organization_id, await add_workspace(service))
    confined = await post(service, "/models", manual(shared["id"], "confined", elsewhere.workspace_id))
    async with short_session(storage) as session:
        with pytest.raises(ServiceError, match="delegation"):
            await resolve_model(session, actor, scope, model["id"], authority=reader)
        with pytest.raises(ServiceError) as hidden:
            await resolve_model(session, actor, scope, confined["id"])
    assert hidden.value.code == "not_found"

    provider_item = f"{service.organization}/model-providers/{shared['id']}"
    current = (await service.client.get(provider_item)).headers["etag"]
    response = await service.client.patch(provider_item, json={"enabled": False}, headers={"if-match": current})
    assert response.status_code == 200, response.text
    async with short_session(storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, actor, scope, model["id"])
    assert refused.value.details == {"kind": "model_provider", "id": shared["id"]}


async def test_model_updates_validate_the_api_and_replace_pricing_and_catalog_ref(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    body = {
        **manual(shared["id"], "gpt", None, model_name="gpt-5.5", model_api="openai.responses"),
        "pricing": price("gpt-5.5"),
        "catalog_ref": {"provider": "openai", "model": "gpt-5.5"},
    }
    model = await post(service, "/models", body)
    item = f"{service.organization}/models/{model['id']}"
    config = {**model["config"], "model_api": "anthropic.messages"}
    refused = await service.client.patch(item, json={"config": config}, headers={"if-match": etag(model)})
    assert refused.status_code == 400 and refused.json()["error"]["details"]["field"] == "config.model_api"

    config["model_api"] = "openai.chat_completions"
    updated = await service.client.patch(
        item, json={"config": config, "pricing": None}, headers={"if-match": etag(model)}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config"]["model_api"] == "openai.chat_completions" and updated.json()["pricing"] is None
    assert updated.json()["catalog_ref"] == body["catalog_ref"] and updated.json()["version"] == model["version"] + 1

    compatible = {"provider": "deepseek", "model": "deepseek-v4-pro"}
    replaced = await service.client.patch(
        item, json={"catalog_ref": compatible}, headers={"if-match": updated.headers["etag"]}
    )
    assert replaced.status_code == 200 and replaced.json()["catalog_ref"] == compatible
    removed = await service.client.patch(
        item, json={"catalog_ref": None}, headers={"if-match": replaced.headers["etag"]}
    )
    assert removed.status_code == 200 and removed.json()["catalog_ref"] is None


async def test_models_carry_a_description_and_may_start_disabled(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    body = {**manual(shared["id"], "draft", None), "description": "Staged rollout", "enabled": False}
    model = await post(service, "/models", body)
    assert (model["description"], model["enabled"]) == ("Staged rollout", False)
    # Created disabled, it is never usable by runs before it is reviewed and enabled.
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    async with short_session(service.runtime.storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, admin(service), scope, model["id"])
    assert refused.value.code == "disabled"

    item = f"{service.organization}/models/{model['id']}"
    described = await service.client.patch(item, json={"description": "Ready"}, headers={"if-match": etag(model)})
    assert described.status_code == 200 and described.json()["description"] == "Ready"
    too_long = await service.client.patch(
        item, json={"description": "x" * 2049}, headers={"if-match": described.headers["etag"]}
    )
    assert too_long.status_code == 400
    assert (await post(service, "/models", manual(shared["id"], "plain", None)))["description"] == ""


async def test_calls_are_attributed_to_the_model_they_select_whatever_upstream_model_answers(
    executing, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    """A parent and its inline child call the same upstream model through two provider accounts, and the endpoint
    answers under another model name, as a dated snapshot would. Each call is admitted, attributed and priced as
    the model that selected it; the record keeps the reported name as information."""
    endpoint = {"config": {"base_url": scripted_model.url}, "credential": {"api_key": "sk-scripted"}}
    models = {}
    for key, input_mtok in (("parent", "5"), ("child", "10")):
        account = await provider(executing, None, name=key, **endpoint)
        models[key] = await post(
            executing,
            "/models",
            {**manual(account["id"], key, None, model_name="gpt-5"), "pricing": price("gpt-5", input_mtok=input_mtok)},
        )
    child = await runs_kit.add_agent(executing, "child", models["child"]["id"], instructions="Role: child")
    parent = await runs_kit.add_agent(
        executing,
        "parent",
        models["parent"]["id"],
        instructions="Role: parent",
        subagent_mode="inline",
        subagents={"helper": {"agent_id": child["id"], "description": "Computes answers"}},
    )
    scripted_model.call("delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="parent")
    scripted_model.say("42", to="child")
    scripted_model.say("The helper said 42", to="parent")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, parent, "ask"))["run"]["id"])
    assert run["status"] == "completed" and run["output"] == "The helper said 42", run

    usage = await executing.client.get(f"{executing.workspace}/usage", params={"run_id": run["id"]})
    used = {item["model_id"]: (item["requests"], Decimal(str(item["cost"]))) for item in usage.json()["models"]}
    # 12 input and 5 output tokens per call, at 5 (parent) or 10 (child) and 30 USD per million.
    assert used == {models["parent"]["id"]: (2, Decimal("0.00042")), models["child"]["id"]: (1, Decimal("0.00027"))}
    async with transaction(executing.runtime.storage) as session:
        records = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == run["id"], UsageRecordRow.record["kind"].astext == "model"
                )
            )
        ).all()
    assert {record.record["model_name"] for record in records} == {"scripted"}


async def test_a_tool_review_is_a_call_of_the_reviewer_model(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """The reviewer's request spends the reviewer model's credential: it is recorded, attributed and priced as that
    model, like the agent's own requests."""
    endpoint = {"config": {"base_url": scripted_model.url}, "credential": {"api_key": "sk-scripted"}}
    models = {}
    for key, input_mtok in (("agent", "5"), ("reviewer", "10")):
        account = await provider(executing, None, name=key, **endpoint)
        models[key] = await post(
            executing,
            "/models",
            {**manual(account["id"], key, None, model_name="gpt-5"), "pricing": price("gpt-5", input_mtok=input_mtok)},
        )
    agent = await runs_kit.add_agent(
        executing,
        "reviewed",
        models["agent"]["id"],
        instructions="Role: agent",
        toolsets={"configuration": {"tools": {"find": {"permission": "review"}}}},
        reviewer={"model": models["reviewer"]["id"]},
    )
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find", to="Role: agent")
    scripted_model.call("submit_tool_review", {"risk": "low"}, call_id="call_review", to="submit_tool_review")
    scripted_model.say("Found them", to="Role: agent")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "find models"))["run"]["id"])
    assert run["status"] == "completed" and run["output"] == "Found them", run

    usage = await executing.client.get(f"{executing.workspace}/usage", params={"run_id": run["id"]})
    used = {item["model_id"]: (item["requests"], Decimal(str(item["cost"]))) for item in usage.json()["models"]}
    # 12 input and 5 output tokens per call, at 5 (agent) or 10 (reviewer) and 30 USD per million.
    assert used == {models["agent"]["id"]: (2, Decimal("0.00042")), models["reviewer"]["id"]: (1, Decimal("0.00027"))}
    async with transaction(executing.runtime.storage) as session:
        records = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == run["id"], UsageRecordRow.record["kind"].astext == "model"
                )
            )
        ).all()
    [review] = [record for record in records if record.record["source"] == "tool.review"]
    assert (review.record["kind"], review.model_id) == ("model", models["reviewer"]["id"])
    assert review.record["tool_call_id"] == "call_find"


async def test_a_compaction_is_a_call_of_the_agent_model(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """Compaction asks the agent's own model for a summary: that call is admitted, attributed and priced as the
    agent's model, like the requests it summarizes."""
    endpoint = {"config": {"base_url": scripted_model.url}, "credential": {"api_key": "sk-scripted"}}
    account = await provider(executing, None, name="compacting", **endpoint)
    model = await post(
        executing,
        "/models",
        {**manual(account["id"], "compacting", None, model_name="gpt-5"), "pricing": price("gpt-5")},
    )
    # The 17 tokens of the first answer pass half of a 20-token window, so the next request compacts first.
    characteristics = {"context_window_tokens": 20, "compact_threshold": 0.5}
    agent = await runs_kit.add_agent(
        executing,
        "compacting",
        model["id"],
        model={"model_id": model["id"], "characteristics": characteristics},
        toolsets={"configuration": {"tools": {"find": {}}}},
    )
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find")
    scripted_model.say("Found models so far.", to="compact continuation summary")
    scripted_model.say("Done")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "find models"))["run"]["id"])
    assert run["status"] == "completed" and run["output"] == "Done", run

    usage = await executing.client.get(f"{executing.workspace}/usage", params={"run_id": run["id"]})
    [used] = usage.json()["models"]
    # Three calls, the compaction included, of 12 input and 5 output tokens at 5 and 30 USD per million.
    assert (used["model_id"], used["requests"], Decimal(str(used["cost"]))) == (model["id"], 3, Decimal("0.00063"))


async def test_a_model_price_applies_to_its_own_calls_whatever_the_entry_names(
    executing, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    """Execution prices each call by the model it selected; the entry's provider and model record only where the
    prices came from. Both models call the scripted endpoint: a compatible catalog pick under an `openai`
    provider, and a `moonshot` provider."""
    for type_, key, source in (
        ("openai", "compatible", price("MiniMax-M3", provider="minimax")),
        ("moonshot", "kimi", price("kimi-k3", provider="moonshot")),
    ):
        endpoint = {"config": {"base_url": scripted_model.url}, "credential": {"api_key": "sk-scripted"}}
        created = await provider(executing, None, type=type_, name=key, **endpoint)
        model = await post(
            executing, "/models", {**manual(created["id"], key, None, model_name="scripted"), "pricing": source}
        )
        assert (model["pricing"]["provider"], model["pricing"]["model"]) == (source["provider"], source["model"])
        agent = await runs_kit.add_agent(executing, key, model["id"])
        scripted_model.say("Done")
        run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "hi"))["run"]["id"])
        assert run["status"] == "completed", run
        usage = await executing.client.get(f"{executing.workspace}/usage", params={"run_id": run["id"]})
        [used] = usage.json()["models"]
        # 12 input and 5 output tokens at 5 and 30 USD per million.
        assert (used["model_id"], Decimal(str(used["cost"]))) == (model["id"], Decimal("0.00021"))


async def test_open_model_builds_the_native_model_with_the_revealed_secrets(service) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    # Building makes no request; a loopback endpoint keeps the endpoint policy independent of local DNS.
    shared = await provider(
        service, None, config={"base_url": "http://127.0.0.1:9/v1"}, extra_headers={"x-gateway-key": "gw-secret"}
    )
    model = await post(
        service, "/models", manual(shared["id"], "gpt", None, model_name="gpt-5.5", model_api="openai.responses")
    )
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    async with short_session(runtime.storage) as session:
        resolved = await resolve_model(session, admin(service), scope, model["id"])
    async with open_model(
        resolved,
        registry=runtime.registry,
        keys=runtime.keys,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    ) as native:
        assert isinstance(native, OpenAIResponsesModel)
        assert native.model_name == "gpt-5.5"
        assert native.client.api_key == SECRET
        assert native.client.default_headers["x-gateway-key"] == "gw-secret"
        assert str(native.client.base_url) == "http://127.0.0.1:9/v1/"


async def test_agents_reference_models_through_model_resolution(service) -> None:  # type: ignore[no-untyped-def]
    shared = await provider(service)
    model = await post(service, "/models", manual(shared["id"], "gpt", None))
    agents = f"{service.workspace}/agents"
    created = await service.client.post(
        agents, json={"key": "helper", "name": "Helper", "config": {"model": {"model_id": model["id"]}}}
    )
    assert created.status_code == 201, created.text

    elsewhere = await post(service, "/models", manual(shared["id"], "elsewhere", await add_workspace(service)))
    hidden = await service.client.post(
        agents, json={"key": "hidden", "name": "Hidden", "config": {"model": {"model_id": elsewhere["id"]}}}
    )
    assert hidden.status_code == 400 and hidden.json()["error"]["details"]["kind"] == "model"

    await service.client.patch(
        f"{service.organization}/models/{model['id']}", json={"enabled": False}, headers={"if-match": etag(model)}
    )
    refused = await service.client.post(
        agents, json={"key": "late", "name": "Late", "config": {"model": {"model_id": model["id"]}}}
    )
    assert refused.status_code == 400 and refused.json()["error"]["details"]["kind"] == "model"
