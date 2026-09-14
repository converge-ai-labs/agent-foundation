"""Saved settings and layered overrides must reach the actual upstream request."""

import hashlib

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "override,temperature",
    [({}, 0.2), ({"settings": {}}, 0.2), ({"settings": None}, 0.3), ({"settings": {"temperature": 0.5}}, 0.5)],
)
async def test_model_agent_run_settings_precedence(model_lab, override, temperature):
    journey = model_lab
    case = await journey.model(settings={"temperature": 0.3, "max_tokens": 128})
    agent = await journey.agent(model={"model_key": case["model"]["key"], "settings": {"temperature": 0.2}})
    assert (await journey.invoke(case, agent=agent, overrides={"model": override}))["output_text"] == case["answer"]
    body = journey.requests(case, inference=True)[-1]["body"]
    assert body["temperature"] == temperature and body["max_completion_tokens"] == 128


async def test_nested_override_replaces_object_and_model_patch_clears_defaults(model_lab):
    journey = model_lab
    case = await journey.model(settings={"temperature": 0.3, "extra_body": {"custom": {"from_model": 1}}})
    agent = await journey.agent(
        model={"model_key": case["model"]["key"], "settings": {"extra_body": {"custom": {"from_agent": 2}}}}
    )
    await journey.invoke(
        case, agent=agent, overrides={"model": {"settings": {"extra_body": {"custom": {"from_run": 3}}}}}
    )
    assert journey.requests(case, inference=True)[-1]["body"]["custom"] == {"from_run": 3}
    path = journey.base + "/models/" + case["model"]["id"]
    await journey.patch(path, {"name": case["model"]["name"] + " renamed"})
    assert (await journey.live.request("GET", path))["settings"] == case["model"]["settings"]
    await journey.patch(path, {"settings": {}})
    await journey.invoke(case)
    body = journey.requests(case, inference=True)[-1]["body"]
    assert "temperature" not in body and "custom" not in body


@pytest.mark.parametrize(
    "settings",
    [
        {"temperature": "INVALID_PRIVATE_VALUE"},
        {"unknown_parameter": 1},
        {"extra_body": {"model": "INVALID_PRIVATE_VALUE"}},
        {"extra_body": {"tool_choice": "none"}},
    ],
)
async def test_invalid_settings_fail_before_storage_or_dispatch(model_lab, settings):
    journey = model_lab
    case = await journey.model()
    path = journey.base + "/models/" + case["model"]["id"]
    before = await journey.live.http.get(path)
    response = await journey.live.http.patch(
        path, json={"settings": settings}, headers={"If-Match": before.headers["etag"]}
    )
    assert response.status_code == 400 and "INVALID_PRIVATE_VALUE" not in response.text
    assert await journey.live.request("GET", path) == before.json()
    assert not journey.requests(case, inference=True)


async def test_switching_model_key_uses_selected_defaults_and_retains_agent_overrides(model_lab):
    journey = model_lab
    first = await journey.model(settings={"max_tokens": 64})
    second = await journey.model(settings={"max_tokens": 256})
    agent = await journey.agent(model={"model_key": first["model"]["key"], "settings": {"temperature": 0.2}})
    assert (await journey.invoke(second, agent=agent, overrides={"model": {"model_key": second["model"]["key"]}}))[
        "output_text"
    ] == second["answer"]
    assert not journey.requests(first, inference=True)
    body = journey.requests(second, inference=True)[-1]["body"]
    assert body["max_completion_tokens"] == 256 and body["temperature"] == 0.2


@pytest.mark.parametrize("mode", ["bearer", "api_key_header", "none"])
async def test_authentication_mode_and_header_rotation_reach_all_operations(model_lab, mode):
    journey = model_lab
    configuration = {"auth_mode": mode}
    if mode == "api_key_header":
        configuration["api_key_header_name"] = "x-model-key"
    case = await journey.model(
        configuration=configuration,
        credential=None if mode == "none" else "fixture-primary",
        headers={"x-team": "retained", "x-gateway-key": "initial"},
    )
    provider_path = journey.base + "/model-providers/" + case["provider"]["id"]
    for action in ["test", "discover-models"]:
        await journey.post(provider_path + "/" + action, {}, expected=200)
    await journey.invoke(case)

    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    for request in journey.requests(case):
        headers = request["headers_sha256"]
        assert headers["x-team"] == digest("retained") and headers["x-gateway-key"] == digest("initial")
        assert headers.get("authorization") == (digest("Bearer fixture-primary") if mode == "bearer" else None)
        assert headers.get("x-model-key") == (digest("fixture-primary") if mode == "api_key_header" else None)
    saved = await journey.patch(provider_path, {"extra_headers": {"x-gateway-key": "rotated"}})
    assert "rotated" not in str(saved) and set(saved["header_names"]) == {"x-team", "x-gateway-key"}
    await journey.invoke(case)
    headers = journey.requests(case, inference=True)[-1]["headers_sha256"]
    assert headers["x-team"] == digest("retained") and headers["x-gateway-key"] == digest("rotated")
    await journey.patch(
        provider_path,
        {
            "configuration": {**case["provider"]["configuration"], "auth_mode": "none", "api_key_header_name": None},
            "credential": None,
            "extra_headers": {"x-gateway-key": None},
        },
    )
    await journey.invoke(case)
    headers = journey.requests(case, inference=True)[-1]["headers_sha256"]
    assert headers == {"x-team": digest("retained")}


async def test_incompatible_api_and_retained_agent_settings_fail_before_dispatch(model_lab):
    from ..infrastructure.client import agent_input

    journey = model_lab
    case = await journey.model(api="openai.responses")
    path = journey.base + "/models/" + case["model"]["id"]
    before = await journey.live.http.get(path)
    response = await journey.live.http.patch(
        path, json={"model_api": "anthropic.messages"}, headers={"If-Match": before.headers["etag"]}
    )
    assert response.status_code == 400
    assert await journey.live.request("GET", path) == before.json()
    agent = await journey.agent(model={"model_key": case["model"]["key"], "settings": {"openai_text_verbosity": "low"}})
    await journey.patch(path, {"model_api": "openai.chat_completions"})
    await journey.post(
        journey.base + "/runs",
        {"agent_id": agent["agent"]["id"], "input": agent_input("Reject incompatible retained settings")},
        expected=400,
    )
    assert not journey.requests(case, inference=True)
