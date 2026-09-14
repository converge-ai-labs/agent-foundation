"""Model authoring commands through real Control HTTP and an owned upstream."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


async def test_provider_catalog_description_model_test_and_run(model_lab):
    journey = model_lab
    case = await journey.model(settings={"temperature": 0.3, "max_tokens": 128})
    provider_path = journey.base + "/model-providers/" + case["provider"]["id"]
    model_path = journey.base + "/models/" + case["model"]["id"]
    before = await journey.live.request("GET", model_path)
    providers = await journey.live.collection(journey.base + "/model-providers")
    assert case["provider"]["id"] in {item["id"] for item in providers}
    probe = await journey.post(provider_path + "/test", {}, expected=200)
    assert probe["success"] and probe["code"] == "connection_succeeded"
    assert len(journey.requests(case)) == 1, "Provider test must read only one catalog page"
    catalog = await journey.post(provider_path + "/discover-models", {}, expected=200)
    assert [item["upstream_model"] for item in catalog["items"]] == ["fixture-a", "fixture-z", "manual-model"]
    assert set(catalog["settings_schemas"]) == {"openai.responses", "openai.chat_completions"}
    assert "next_cursor" not in catalog
    assert len(journey.requests(case)) == 3
    description = await journey.post(
        provider_path + "/describe-model",
        {
            "upstream_model": "not-in-the-catalog",
            "model_api": "openai.chat_completions",
        },
        expected=200,
    )
    assert description["settings_schema"] and description["suggested_model_api"] == "openai.chat_completions"
    assert not journey.requests(case, inference=True), "Description and discovery cannot perform inference"
    models = await journey.live.collection(journey.base + "/models")
    assert not any(item["upstream_model"] == "not-in-the-catalog" for item in models)
    assert await journey.live.request("GET", model_path) == before
    tested = await journey.post(model_path + "/test", {}, expected=200)
    assert tested["success"] and tested["code"] == "connection_succeeded"
    body = journey.requests(case, inference=True)[0]["body"]
    assert body["model"] == "manual-model" and body["temperature"] == 0.3 and body["max_completion_tokens"] == 128
    assert not body.get("stream")
    run = await journey.invoke(case)
    assert run["output_text"] == case["answer"]
    assert journey.requests(case, inference=True)[-1]["body"]["stream"] is True
    assert await journey.live.request("GET", model_path) == before


@pytest.mark.parametrize("catalog_status", [404, 503])
async def test_failed_discovery_keeps_manual_model_usable(model_lab, catalog_status):
    journey = model_lab
    case = await journey.model(catalog_status=catalog_status)
    path = journey.base + "/model-providers/" + case["provider"]["id"]
    response = await journey.live.http.post(path + "/discover-models")
    assert response.status_code == 502 and response.json()["error"]["code"] == "provider_discovery_failed"
    description = await journey.post(path + "/describe-model", {"upstream_model": "manual-model"}, expected=200)
    assert description["settings_schema"]
    assert (await journey.invoke(case))["output_text"] == case["answer"]


async def test_empty_catalog_is_success_and_not_an_allowlist(model_lab):
    journey = model_lab
    case = await journey.model(empty_catalog=True)
    path = journey.base + "/model-providers/" + case["provider"]["id"]
    assert (await journey.post(path + "/discover-models", {}, expected=200))["items"] == []
    assert (await journey.invoke(case))["output_text"] == case["answer"]


async def test_model_test_reports_upstream_rejection_without_mutating_configuration(model_lab):
    journey = model_lab
    case = await journey.model(status=401)
    path = journey.base + "/models/" + case["model"]["id"]
    before = await journey.live.request("GET", path)
    tested = await journey.post(path + "/test", {}, expected=200)
    assert tested["success"] is False and tested["code"] == "connection_failed"
    assert "Fixture upstream rejection" not in str(tested)
    assert len(journey.requests(case, inference=True)) == 1
    assert await journey.live.request("GET", path) == before


@pytest.mark.parametrize("resource", ["model", "provider"])
async def test_concurrent_etag_updates_have_one_winner(model_lab, resource):
    journey = model_lab
    case = await journey.model()
    collection = "models" if resource == "model" else "model-providers"
    path = f"{journey.base}/{collection}/{case[resource]['id']}"
    original = await journey.live.http.get(path)
    changes = [{"name": "Winner A " + case[resource]["id"]}, {"name": "Winner B " + case[resource]["id"]}]
    responses = await asyncio.gather(
        *(
            journey.live.http.patch(path, json=value, headers={"If-Match": original.headers["etag"]})
            for value in changes
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 412]
    winner = next(response for response in responses if response.status_code == 200)
    loser = next(response for response in responses if response.status_code == 412)
    assert loser.json()["error"]["code"] == "precondition_failed"
    assert await journey.live.request("GET", path) == winner.json()
    assert (await journey.live.http.patch(path, json={"enabled": False})).status_code == 400
    assert await journey.live.request("GET", path) == winner.json()


async def test_concurrent_model_key_creation_has_one_winner(model_lab):
    journey = model_lab
    case = await journey.model()
    body = {key: case["model"][key] for key in ["name", "provider_id", "upstream_model", "model_api", "settings"]}
    body["key"] = case["model"]["key"] + "-race"
    responses = await asyncio.gather(*(journey.live.http.post(journey.base + "/models", json=body) for _ in range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["error"]["code"] == "model_key_conflict"


@pytest.mark.parametrize("change", [{"key": "renamed"}, {"provider_id": "mprov_missing"}])
async def test_model_identity_is_immutable(model_lab, change):
    journey = model_lab
    case = await journey.model()
    path = journey.base + "/models/" + case["model"]["id"]
    before = await journey.live.http.get(path)
    response = await journey.live.http.patch(path, json=change, headers={"If-Match": before.headers["etag"]})
    assert response.status_code == 400
    assert await journey.live.request("GET", path) == before.json()


async def test_disabled_model_can_be_reenabled_without_recreation(model_lab):
    journey = model_lab
    case = await journey.model()
    path = journey.base + "/models/" + case["model"]["id"]
    await journey.patch(path, {"enabled": False})
    result = await journey.post(path + "/test", {}, expected=200)
    assert not result["success"] and not journey.requests(case, inference=True)
    await journey.patch(path, {"enabled": True})
    assert (await journey.invoke(case))["output_text"] == case["answer"]
