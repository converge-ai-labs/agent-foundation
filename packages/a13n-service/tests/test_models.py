"""Models: keys, creation and updates, the provider of their workspace, resolution and execution."""

from decimal import Decimal

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.models.runtime import open_model
from a13n_service.resources.models.schemas import ModelConfig, ModelCreate, ModelUpdate
from a13n_service.resources.models.service import create_model, resolve_model, update_model
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.tables import UsageRecordRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, ExecutionAuthority, Grant, Principal, WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic_ai.models.openai import OpenAIResponsesModel
from sqlalchemy import select

pytestmark = pytest.mark.anyio

SECRET = "sk-model-secret"


def etag(resource: dict) -> str:
    """A provider's ETag names its ID, a model's its key."""
    return f'"{resource["id"] if "id" in resource else resource["key"]}:{resource["version"]}"'


async def post(service, path: str, body: dict, status: int = 201, workspace_id: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    """`workspace_id` names another workspace than the session's own."""
    headers = {"x-workspace-id": workspace_id} if workspace_id else {}
    response = await service.client.post(service.api + path, json=body, headers=headers)
    assert response.status_code == status, response.text
    return response.json()


async def provider(service, workspace_id: str | None = None, **changes: object) -> dict:  # type: ignore[no-untyped-def]
    body = {"type": "openai", "name": "OpenAI", "credential": {"api_key": SECRET}}
    return await post(service, "/model-providers", {**body, **changes}, workspace_id=workspace_id)


def manual(provider_id: str, key: str, **config: object) -> dict:
    return {
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
        session.add(WorkspaceRow(id=workspace_id, organization_id=service.tenant.organization_id, name="Second"))
    return workspace_id


def admin(service) -> Principal:  # type: ignore[no-untyped-def]
    return Principal(
        service.tenant.principal_id, "user", (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES["admin"]),)
    )


async def test_models_are_created_with_their_configuration_and_the_catalog_model_they_started_from(service) -> None:  # type: ignore[no-untyped-def]
    account = await provider(service)
    body = {
        **manual(account["id"], "gpt-5-5", model_name="gpt-5.5", characteristics={"context_window_tokens": 1050000}),
        "pricing": price("gpt-5.5"),
        "catalog_ref": {"provider": "openai", "model": "gpt-5.5"},
    }
    created = await post(service, "/models", body)
    assert created["workspace_id"] == service.tenant.workspace_id and "id" not in created
    assert created["config"]["model_name"] == "gpt-5.5"
    assert created["config"]["characteristics"]["context_window_tokens"] == 1050000
    assert created["pricing"]["model"] == "gpt-5.5"
    assert created["catalog_ref"] == {"provider": "openai", "model": "gpt-5.5"}

    # The reference is provenance, checked for shape only: the catalog may list the model no more, or never have.
    unlisted = {**body, "key": "gateway", "catalog_ref": {"provider": "openrouter", "model": "vendor/unlisted"}}
    gateway = await post(service, "/models", unlisted)
    assert gateway["catalog_ref"] == unlisted["catalog_ref"]
    await post(
        service, "/models", {**body, "key": "bad-ref", "catalog_ref": {"provider": "Open AI", "model": "x"}}, 400
    )
    await post(service, "/models", {**body, "key": "catalog-key", "catalog_key": "openai:gpt-5.5"}, status=400)
    await post(service, "/models", {key: value for key, value in body.items() if key != "config"}, status=400)

    plain = await post(service, "/models", manual(account["id"], "custom"))
    assert plain["pricing"] is None and plain["catalog_ref"] is None
    unsupported = await post(
        service, "/models", manual(account["id"], "messages", model_api="anthropic.messages"), status=400
    )
    assert unsupported["error"]["details"]["field"] == "config.model_api"

    listed = (await service.client.get(f"{service.api}/models")).json()
    assert [item["key"] for item in listed["items"]] == ["custom", "gateway", "gpt-5-5"]


async def test_a_model_key_defaults_to_its_provider_type_and_upstream_name(service) -> None:  # type: ignore[no-untyped-def]
    account = await provider(service)
    config = {"model_name": "anthropic/Claude Opus_5.1", "model_api": "openai.chat_completions"}
    keyless = {"provider_id": account["id"], "name": "Opus", "config": config}
    # Every run of characters a key cannot hold becomes `-`; dots stay.
    defaulted = await post(service, "/models", keyless)
    assert defaulted["key"] == "openai-anthropic-claude-opus-5.1"
    assert (await post(service, "/models", {**keyless, "key": "opus.fast"}))["key"] == "opus.fast"
    duplicate = await post(service, "/models", keyless, status=409)
    assert duplicate["error"]["code"] == "already_exists"
    for key in ("Opus", "opus_fast", "-opus", "x" * 129):
        await post(service, "/models", {**keyless, "key": key}, status=400)

    key = defaulted["key"]
    item = f"{service.api}/models/{key}"
    read = await service.client.get(item)
    assert read.status_code == 200 and read.headers["etag"] == etag(defaulted) == f'"{key}:1"'
    stale = await service.client.patch(item, json={"name": "Claude"}, headers={"if-match": f'"{key}:0"'})
    assert stale.status_code == 412
    renamed = await service.client.patch(item, json={"name": "Claude"}, headers={"if-match": etag(defaulted)})
    assert renamed.status_code == 200 and renamed.headers["etag"] == f'"{key}:2"', renamed.text
    # The key is immutable.
    rekeyed = await service.client.patch(item, json={"key": "opus"}, headers={"if-match": renamed.headers["etag"]})
    assert rekeyed.status_code == 400
    assert (await service.client.get(f"{service.api}/models/opus")).status_code == 404


async def test_model_lists_page_through_keys_of_every_valid_length(service) -> None:  # type: ignore[no-untyped-def]
    account = await provider(service)
    keys = ["a" * 128, "b" * 128, "c"]
    for key in keys:
        await post(service, "/models", manual(account["id"], key))
    listed, cursor = [], None
    while True:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        page = await service.client.get(f"{service.api}/models", params=params)
        assert page.status_code == 200, page.text
        listed += [item["key"] for item in page.json()["items"]]
        if not (cursor := page.json()["next_cursor"]):
            break
    assert listed == keys


async def test_a_model_is_served_by_a_provider_of_its_workspace(service) -> None:  # type: ignore[no-untyped-def]
    first, second = service.tenant.workspace_id, await add_workspace(service)
    own = await provider(service)
    elsewhere = await provider(service, second)
    hidden = await post(service, "/models", manual(elsewhere["id"], "model"), status=404)
    assert hidden["error"]["details"] == {"kind": "model_provider", "id": elsewhere["id"]}
    await post(service, "/models", manual(own["id"], "model"))
    # Keys are unique per workspace only, and a key names the model of the request's workspace.
    assert (await post(service, "/models", manual(elsewhere["id"], "model"), workspace_id=second))["key"] == "model"
    item, in_second = f"{service.api}/models/model", {"x-workspace-id": second}
    theirs = await service.client.get(item, headers=in_second)
    assert theirs.json()["provider_id"] == elsewhere["id"]
    renamed = await service.client.patch(
        item, json={"name": "Theirs"}, headers={"if-match": theirs.headers["etag"], **in_second}
    )
    assert renamed.status_code == 200, renamed.text
    mine = (await service.client.get(item)).json()
    assert (mine["provider_id"], mine["name"]) == (own["id"], "model")

    disabled = await provider(service, name="Disabled")
    patched = await service.client.patch(
        f"{service.api}/model-providers/{disabled['id']}",
        json={"enabled": False},
        headers={"if-match": etag(disabled)},
    )
    assert patched.status_code == 200, patched.text
    refused = await post(service, "/models", manual(disabled["id"], "paused"), status=422)
    assert refused["error"]["details"] == {"kind": "model_provider", "id": disabled["id"]}

    # A model spends its provider's credential, so only the workspace's writers configure one.
    storage, organization_id, registry = (
        service.runtime.storage,
        service.tenant.organization_id,
        service.runtime.registry,
    )

    def member(role: str) -> Principal:
        return Principal(service.tenant.principal_id, "user", (Grant(organization_id, first, BUILT_IN_ROLES[role]),))

    body = ModelCreate.model_validate(manual(own["id"], "built"))
    with pytest.raises(ServiceError, match="cannot perform"):
        await create_model(storage, member("runner"), first, body, registry=registry)
    built = await create_model(storage, member("builder"), first, body, registry=registry)
    assert built.workspace_id == first
    config = ModelConfig.model_validate({**built.config.model_dump(), "max_tokens": 1024})
    with pytest.raises(ServiceError, match="cannot perform"):
        await update_model(
            storage,
            member("runner"),
            first,
            "built",
            ModelUpdate(config=config),
            if_match=f'"built:{built.version}"',
            registry=registry,
        )
    updated = await update_model(
        storage,
        member("builder"),
        first,
        "built",
        ModelUpdate(config=config),
        if_match=f'"built:{built.version}"',
        registry=registry,
    )
    assert updated.config.max_tokens == 1024


async def test_models_resolve_for_execution_only_while_enabled_and_in_scope(service) -> None:  # type: ignore[no-untyped-def]
    organization_id, workspace_id = service.tenant.organization_id, service.tenant.workspace_id
    scope = WorkspaceScope(organization_id, workspace_id)
    account = await provider(service)
    model = await post(service, "/models", manual(account["id"], "gpt", max_tokens=1024))
    item = f"{service.api}/models/gpt"

    assert (await service.client.patch(item, json={"enabled": False})).status_code == 428
    disabled = await service.client.patch(
        item, json={"enabled": False, "name": "Paused"}, headers={"if-match": etag(model)}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False and disabled.json()["name"] == "Paused"
    storage, actor = service.runtime.storage, admin(service)
    async with short_session(storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, actor, scope, "gpt")
    assert refused.value.code == "disabled"

    enabled = await service.client.patch(item, json={"enabled": True}, headers={"if-match": disabled.headers["etag"]})
    assert enabled.status_code == 200, enabled.text
    async with short_session(storage) as session:
        resolved = await resolve_model(session, actor, scope, "gpt")
    assert resolved.key == "gpt" and resolved.config.max_tokens == 1024 and resolved.provider.id == account["id"]
    assert resolved.provider.reveal_credential(service.runtime.keys) == {"api_key": SECRET}
    assert SECRET not in repr(resolved)

    # Execution authority narrows what the run's principal may use.
    reader = ExecutionAuthority(
        principal_id=actor.id, organization_id=organization_id, workspace_id=workspace_id, verbs=frozenset({"read"})
    )
    elsewhere = await add_workspace(service)
    await post(
        service, "/models", manual((await provider(service, elsewhere))["id"], "confined"), workspace_id=elsewhere
    )
    async with short_session(storage) as session:
        with pytest.raises(ServiceError, match="delegation"):
            await resolve_model(session, actor, scope, "gpt", authority=reader)
        with pytest.raises(ServiceError) as hidden:
            await resolve_model(session, actor, scope, "confined")
    assert hidden.value.code == "not_found"

    provider_item = f"{service.api}/model-providers/{account['id']}"
    current = (await service.client.get(provider_item)).headers["etag"]
    response = await service.client.patch(provider_item, json={"enabled": False}, headers={"if-match": current})
    assert response.status_code == 200, response.text
    async with short_session(storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, actor, scope, "gpt")
    assert refused.value.details == {"kind": "model_provider", "id": account["id"]}


async def test_model_updates_validate_the_api_and_replace_pricing_and_catalog_ref(service) -> None:  # type: ignore[no-untyped-def]
    account = await provider(service)
    body = {
        **manual(account["id"], "gpt", model_name="gpt-5.5", model_api="openai.responses"),
        "pricing": price("gpt-5.5"),
        "catalog_ref": {"provider": "openai", "model": "gpt-5.5"},
    }
    model = await post(service, "/models", body)
    item = f"{service.api}/models/gpt"
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
    account = await provider(service)
    body = {**manual(account["id"], "draft"), "description": "Staged rollout", "enabled": False}
    model = await post(service, "/models", body)
    assert (model["description"], model["enabled"]) == ("Staged rollout", False)
    # Created disabled, it is never usable by runs before it is reviewed and enabled.
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    async with short_session(service.runtime.storage) as session:
        with pytest.raises(ServiceError) as refused:
            await resolve_model(session, admin(service), scope, "draft")
    assert refused.value.code == "disabled"

    item = f"{service.api}/models/draft"
    described = await service.client.patch(item, json={"description": "Ready"}, headers={"if-match": etag(model)})
    assert described.status_code == 200 and described.json()["description"] == "Ready"
    too_long = await service.client.patch(
        item, json={"description": "x" * 2049}, headers={"if-match": described.headers["etag"]}
    )
    assert too_long.status_code == 400
    assert (await post(service, "/models", manual(account["id"], "plain")))["description"] == ""


async def test_calls_are_attributed_to_the_model_they_select_whatever_upstream_model_answers(
    executing, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    """A parent and its inline child call the same upstream model through two provider accounts, and the endpoint
    answers under another model name, as a dated snapshot would. Each call is admitted, attributed and priced as
    the model that selected it; the record keeps the reported name as information."""
    endpoint = {"config": {"base_url": scripted_model.url}, "credential": {"api_key": "sk-scripted"}}
    for key, input_mtok in (("parent", "5"), ("child", "10")):
        account = await provider(executing, name=key, **endpoint)
        await post(
            executing,
            "/models",
            {**manual(account["id"], key, model_name="gpt-5"), "pricing": price("gpt-5", input_mtok=input_mtok)},
        )
    child = await runs_kit.add_agent(executing, "child", "child", instructions="Role: child")
    parent = await runs_kit.add_agent(
        executing,
        "parent",
        "parent",
        instructions="Role: parent",
        subagent_mode="inline",
        subagents={"helper": {"agent_id": child["id"], "description": "Computes answers"}},
    )
    scripted_model.call("delegate", {"subagent": "helper", "prompt": "compute"}, call_id="call_d", to="parent")
    scripted_model.say("42", to="child")
    scripted_model.say("The helper said 42", to="parent")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, parent, "ask"))["run"]["id"])
    assert run["status"] == "completed" and run["output"] == "The helper said 42", run

    usage = await executing.client.get(f"{executing.api}/usage", params={"run_id": run["id"]})
    used = {item["model"]: (item["requests"], Decimal(str(item["cost"]))) for item in usage.json()["models"]}
    # 12 input and 5 output tokens per call, at 5 (parent) or 10 (child) and 30 USD per million.
    assert used == {"parent": (2, Decimal("0.00042")), "child": (1, Decimal("0.00027"))}
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
    for key, input_mtok in (("agent", "5"), ("reviewer", "10")):
        account = await provider(executing, name=key, **endpoint)
        await post(
            executing,
            "/models",
            {**manual(account["id"], key, model_name="gpt-5"), "pricing": price("gpt-5", input_mtok=input_mtok)},
        )
    agent = await runs_kit.add_agent(
        executing,
        "reviewed",
        "agent",
        instructions="Role: agent",
        toolsets={"configuration": {"tools": {"find": {"permission": "review"}}}},
        reviewer={"model": "reviewer"},
    )
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find", to="Role: agent")
    scripted_model.call("submit_tool_review", {"risk": "low"}, call_id="call_review", to="submit_tool_review")
    scripted_model.say("Found them", to="Role: agent")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "find models"))["run"]["id"])
    assert run["status"] == "completed" and run["output"] == "Found them", run

    usage = await executing.client.get(f"{executing.api}/usage", params={"run_id": run["id"]})
    used = {item["model"]: (item["requests"], Decimal(str(item["cost"]))) for item in usage.json()["models"]}
    # 12 input and 5 output tokens per call, at 5 (agent) or 10 (reviewer) and 30 USD per million.
    assert used == {"agent": (2, Decimal("0.00042")), "reviewer": (1, Decimal("0.00027"))}
    async with transaction(executing.runtime.storage) as session:
        records = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.run_id == run["id"], UsageRecordRow.record["kind"].astext == "model"
                )
            )
        ).all()
        reviewer = await session.scalar(select(ModelRow.id).where(ModelRow.key == "reviewer"))
    [review] = [record for record in records if record.record["source"] == "tool.review"]
    assert (review.record["kind"], review.model_id) == ("model", reviewer)
    assert review.record["tool_call_id"] == "call_find"


async def test_a_compaction_is_a_call_of_the_agent_model(executing, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    """Compaction asks the agent's own model for a summary: that call is admitted, attributed and priced as the
    agent's model, like the requests it summarizes."""
    endpoint = {"config": {"base_url": scripted_model.url}, "credential": {"api_key": "sk-scripted"}}
    account = await provider(executing, name="compacting", **endpoint)
    await post(
        executing,
        "/models",
        {**manual(account["id"], "compacting", model_name="gpt-5"), "pricing": price("gpt-5")},
    )
    # The 17 tokens of the first answer pass half of a 20-token window, so the next request compacts first.
    characteristics = {"context_window_tokens": 20, "compact_threshold": 0.5}
    agent = await runs_kit.add_agent(
        executing,
        "compacting",
        "compacting",
        model_characteristics=characteristics,
        toolsets={"configuration": {"tools": {"find": {}}}},
    )
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find")
    scripted_model.say("Found models so far.", to="compact continuation summary")
    scripted_model.say("Done")
    run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "find models"))["run"]["id"])
    assert run["status"] == "completed" and run["output"] == "Done", run

    usage = await executing.client.get(f"{executing.api}/usage", params={"run_id": run["id"]})
    [used] = usage.json()["models"]
    # Three calls, the compaction included, of 12 input and 5 output tokens at 5 and 30 USD per million.
    assert (used["model"], used["requests"], Decimal(str(used["cost"]))) == ("compacting", 3, Decimal("0.00063"))


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
        created = await provider(executing, type=type_, name=key, **endpoint)
        model = await post(
            executing, "/models", {**manual(created["id"], key, model_name="scripted"), "pricing": source}
        )
        assert (model["pricing"]["provider"], model["pricing"]["model"]) == (source["provider"], source["model"])
        agent = await runs_kit.add_agent(executing, key, key)
        scripted_model.say("Done")
        run = await runs_kit.sealed(executing, (await runs_kit.start_thread(executing, agent, "hi"))["run"]["id"])
        assert run["status"] == "completed", run
        usage = await executing.client.get(f"{executing.api}/usage", params={"run_id": run["id"]})
        [used] = usage.json()["models"]
        # 12 input and 5 output tokens at 5 and 30 USD per million.
        assert (used["model"], Decimal(str(used["cost"]))) == (key, Decimal("0.00021"))


async def test_open_model_builds_the_native_model_with_the_revealed_secrets(service) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    # Building makes no request; a loopback endpoint keeps the endpoint policy independent of local DNS.
    account = await provider(
        service, config={"base_url": "http://127.0.0.1:9/v1"}, extra_headers={"x-gateway-key": "gw-secret"}
    )
    await post(service, "/models", manual(account["id"], "gpt", model_name="gpt-5.5", model_api="openai.responses"))
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    async with short_session(runtime.storage) as session:
        resolved = await resolve_model(session, admin(service), scope, "gpt")
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
    account = await provider(service)
    model = await post(service, "/models", manual(account["id"], "gpt"))
    agents = f"{service.api}/agents"
    created = await service.client.post(agents, json={"name": "Helper", "config": {"model": "gpt"}})
    assert created.status_code == 201, created.text

    # A model of another workspace is unknown here.
    elsewhere = await add_workspace(service)
    await post(service, "/models", manual((await provider(service, elsewhere))["id"], "other"), workspace_id=elsewhere)
    hidden = await service.client.post(agents, json={"name": "Hidden", "config": {"model": "other"}})
    assert hidden.status_code == 400 and hidden.json()["error"]["details"]["field"] == "model", hidden.text
    assert hidden.json()["error"]["details"]["kind"] == "model"

    await service.client.patch(f"{service.api}/models/gpt", json={"enabled": False}, headers={"if-match": etag(model)})
    refused = await service.client.post(agents, json={"name": "Late", "config": {"model": "gpt"}})
    assert refused.status_code == 400 and refused.json()["error"]["details"]["kind"] == "model"


async def test_native_model_settings_roundtrip_and_api_validation(service) -> None:  # type: ignore[no-untyped-def]
    account = await provider(service)
    settings = {"thinking": "medium", "openai_store": False, "openai_reasoning_summary": "detailed", "max_tokens": 8192}
    created = await post(
        service, "/models", manual(account["id"], "reasoning", model_api="openai.responses", settings=settings)
    )
    path = f"{service.api}/models/{created['key']}"
    read = await service.client.get(path)
    assert read.json()["config"]["settings"] == settings
    renamed = await service.client.patch(path, json={"name": "Renamed"}, headers={"if-match": read.headers["etag"]})
    assert renamed.status_code == 200
    assert renamed.json()["config"]["settings"] == settings
    changed = await service.client.patch(
        path,
        json={"config": {**created["config"], "settings": {**settings, "openai_store": True}}},
        headers={"if-match": renamed.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["config"]["settings"]["openai_store"] is True
    for rejected in (
        {"openai_store": "false"},
        {"typo": True},
        {"timeout": 10},
        {"openai_previous_response_id": "resp_other"},
        {"extra_body": {"store": True}},
        {"extra_headers": {"Authorization": "secret"}},
        {"extra_headers": {"X-Duplicate": "a", "x-duplicate": "b"}},
    ):
        await post(
            service,
            "/models",
            manual(account["id"], "invalid", model_api="openai.responses", settings=rejected),
            status=400,
        )
    switched = await service.client.patch(
        path,
        json={"config": {**changed.json()["config"], "model_api": "openai.chat_completions"}},
        headers={"if-match": changed.headers["etag"]},
    )
    assert switched.status_code == 400  # Responses summary does not silently survive an API change.
