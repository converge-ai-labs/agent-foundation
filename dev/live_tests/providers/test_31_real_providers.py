"""Additional integration journeys; deterministic fault cases keep their fixtures."""

import json
import logging
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from ..infrastructure.client import agent_input
from ..infrastructure.management_support import has_tool, last_tool_result
from ..infrastructure.round_two_model import matches_tool_name

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)
SEARCH_PROVIDERS = [
    pytest.param("search", id="configured_search_exa"),
    pytest.param("brave_search", id="configured_search_brave"),
]


@pytest.mark.parametrize("configured_provider", SEARCH_PROVIDERS, indirect=True)
async def test_configured_search_account_probe(configured_provider):
    journey, provider = configured_provider
    path = journey.base + f"/search-providers/{provider['id']}"
    persisted = await journey.live.request("GET", path)
    assert persisted["type"] == provider["type"] and persisted["credential_configured"] is True
    assert "credential" not in persisted and persisted["configuration"] == {}
    tested = await journey.post(path + "/test", {}, expected=200)
    assert tested["success"] is True, f"{provider['type']} account probe failed: {tested['code']}"
    assert tested["code"] is None
    assert datetime.fromisoformat(tested["checked_at"]).tzinfo is not None
    assert await journey.live.request("GET", path) == persisted, "Account probe must not mutate the Provider"
    logger.info(
        "%s account probe succeeded: provider=%s checked_at=%s", provider["type"], provider["id"], tested["checked_at"]
    )


@pytest.mark.parametrize("configured_provider", SEARCH_PROVIDERS, indirect=True)
@pytest.mark.parametrize(
    "include_domains,max_results,num",
    [
        pytest.param([], 3, 2, id="requested-limit"),
        pytest.param(["python.org"], 2, 10, id="domain-and-agent-limit"),
        pytest.param(["python.org"], 1, 1, id="single-result"),
    ],
)
async def test_configured_search_run(configured_provider, include_domains, max_results, num):
    journey, provider = configured_provider
    assert provider["type"] in {"exa", "brave"}
    environment, _ = await journey.environment()
    agent = await journey.agent(
        search={"provider_id": provider["id"], "max_results": max_results, "include_domains": include_domains}
    )
    arguments = {"query": "Python asyncio documentation", "num": num}
    # Only the decision is scripted. Neither the search response nor the final
    # answer is supplied to the mock LLM; both must come from the actual tool.
    case = await journey.case(steps=[{"tool": "search", "arguments": arguments}])
    receipt = await journey.start(
        case,
        agent_id=agent["agent"]["id"],
        environment={"environment_id": environment["id"]},
        input=agent_input(
            "LIVE_TEST " + json.dumps(case) + "\nUse web search to find official Python asyncio documentation."
        ),
    )
    completed = await journey.live.finish(receipt["run_id"])
    observations = journey.observations(case)
    assert len(observations) >= 2, "The LLM must receive another request after executing its search call"
    first = observations[0]
    assert first["endpoint"].endswith("/chat/completions") and first["body"]["stream"] is True
    assert has_tool(first, "search")
    assert not any(message.get("role") == "tool" for message in first["body"]["messages"])

    messages = observations[-1]["body"]["messages"]
    calls = [
        call for message in messages if message.get("role") == "assistant" for call in message.get("tool_calls", [])
    ]
    replies = [message for message in messages if message.get("role") == "tool"]
    assert len(calls) == len(replies) == 1, "Expected one LLM search call and its corresponding tool response"
    call = calls[0]
    assert matches_tool_name(call["function"]["name"], "search")
    assert json.loads(call["function"]["arguments"]) == arguments
    assert replies[0]["tool_call_id"] == call["id"], "Search results must return to the originating LLM tool call"
    outcome = last_tool_result(observations[-1])
    assert outcome["ok"] is True, f"{provider['type']} search tool failed: {outcome.get('error')}"
    results = outcome["results"]
    assert isinstance(results, list) and 1 <= len(results) <= min(num, max_results)
    assert outcome["showing"] == len(results)
    for result in results:
        assert isinstance(result["title"], str) and result["title"].strip()
        assert isinstance(result["snippet"], str)
        url = urlsplit(result["url"])
        assert url.scheme in {"http", "https"} and url.hostname
        if include_domains:
            assert any(url.hostname == domain or url.hostname.endswith("." + domain) for domain in include_domains)
        assert result["url"] not in json.dumps(first["body"]), "A source must not be preseeded in the LLM request"

    # The scripted LLM echoes the tool result as its final answer over streamed
    # Chat Completions. Verify the public Run output, not just private observations.
    answer = completed["output_text"]
    while isinstance(answer, str):
        answer = json.loads(answer)
    assert answer == outcome, "The completed Run must expose the search results consumed by the LLM"
    logger.info(
        "Mock LLM -> search -> %s -> LLM final answer succeeded: run=%s tool_call=%s model_requests=%d results=%d requested=%d max_results=%d domains=%s",
        provider["type"],
        receipt["run_id"],
        call["id"],
        len(observations),
        len(outcome["results"]),
        num,
        max_results,
        include_domains,
    )


@pytest.mark.parametrize("configured_provider", ["environment"], indirect=True)
async def test_configured_environment_executes_shell_and_reads_file(configured_provider):
    journey, environment = configured_provider
    proof = "REMOTE_" + uuid4().hex
    case = await journey.case(
        steps=[
            {
                "tool": "shell_exec",
                "arguments": {"command": f"printf '%s' '{proof}' > proof.txt", "yield_time_seconds": 5},
            },
            {"tool": "view", "arguments": {"file_path": "/workspace/proof.txt"}},
        ]
    )
    receipt = await journey.start(case, environment={"environment_id": environment["id"]})
    result = await journey.live.finish(receipt["run_id"])
    assert proof in result["output_text"]
    persisted = await journey.live.request("GET", f"/api/v1/environments/{environment['id']}")
    assert persisted["status"] == "running"


@pytest.mark.parametrize("configured_provider", ["connector"], indirect=True)
async def test_configured_connector_authentication_and_discovery(configured_provider):
    journey, provider = configured_provider
    path = f"/api/v1/connector-providers/{provider['id']}"
    tested = await journey.post(path + "/test", {"expected_version": provider["version"]}, expected=200)
    assert tested["status"] == "succeeded" and tested["verified_access"]
    discovered = await journey.post(path + "/discover-connectors", {}, expected=200)
    assert discovered["items"]
    assert len({item["key"] for item in discovered["items"]}) == len(discovered["items"])


@pytest.mark.parametrize(
    "configured_provider",
    [
        pytest.param("model", id="configured"),
        pytest.param(("model", "openai/gpt-4.1-nano"), id="openrouter-gpt"),
        pytest.param(("model", "google/gemini-2.5-flash-lite"), id="openrouter-gemini"),
        pytest.param(("model", "anthropic/claude-haiku-4.5"), id="openrouter-claude"),
    ],
    indirect=True,
)
async def test_configured_model_completes_real_run(configured_provider):
    journey, model = configured_provider
    logger.info("Model smoke starting: model=%s", model["upstream_model"])
    agent = await journey.agent(model_key=model["key"], instructions="Reply with a brief greeting in plain text.")
    receipt = await journey.post(
        journey.base + "/runs",
        {"agent_id": agent["agent"]["id"], "input": agent_input("Hello")},
        expected=202,
    )
    journey.live.track(receipt)
    result = await journey.live.finish(receipt["run_id"])
    assert result["output_text"].strip(), f"Model {model['upstream_model']} returned empty text"
    logger.info(
        "Model smoke completed: model=%s run=%s output_chars=%d",
        model["upstream_model"],
        receipt["run_id"],
        len(result["output_text"]),
    )
