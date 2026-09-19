"""Real Worker retries and child invocations retain their Bot document authority."""

import json
import os

import pytest

from ..infrastructure.management_support import ManagementJourney
from ..infrastructure.round_two_lab import open_lab
from .journey import converse
from .model import memory_index
from .resources import setup

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mode", ["retry", "inline", "async"])
async def test_bot_memory_reaches_retried_and_delegated_execution(request, mode):
    if not request.config.getoption("--live-management"):
        pytest.skip("Opt in with --live-management for owned Control/Connectivity/Worker processes")
    if not os.environ.get("TEST_MEM0_OSS_URL") or not os.environ.get("TEST_MEM0_OSS_API_KEY"):
        pytest.skip("Set TEST_MEM0_OSS_URL/API_KEY to an explicitly configured native OSS test server")
    async with open_lab(suite="management", local_connectors=False, bot_memory=True) as lab:
        journey, live = ManagementJourney(lab), lab.client
        agent = None
        if mode != "retry":
            child = await journey.agent()
            agent = await journey.agent(subagent_mode=mode, subagents={"child": {"agent_id": child["agent"]["id"]}})
        account, target, _scope, path, document, proof, _agent, provider = await setup(journey, "slack", agent=agent)
        base = f"/api/v1/application-accounts/{account['id']}"
        case = await live.case("bot_memory_retry" if mode == "retry" else "bot_memory_parent")
        if mode != "retry":
            child_case = await live.case("bot_memory_child")
            journey.plan(case, mode=mode, child=child_case)
        changed = False

        async def recover(failed):
            nonlocal changed
            # Switch both routing and Provider after the failed invocation. Retry
            # must retain the accepted selection, not read the new empty Provider.
            replacement = await journey.post(
                journey.base + "/memory-providers",
                {
                    "type": "mem0_oss",
                    "name": "Replacement Bot Provider",
                    "configuration": {"base_url": os.environ["TEST_MEM0_OSS_URL"]},
                    "credential": {"api_key": os.environ["TEST_MEM0_OSS_API_KEY"]},
                },
            )
            replacement_agent = await journey.agent(instructions="REPLACEMENT_AGENT_MUST_NOT_EXECUTE")
            current = await live.request("GET", base + f"/targets/{target['id']}")
            await live.request(
                "PUT",
                base + f"/targets/{target['id']}",
                json={
                    "expected_version": current["version"],
                    "target_kind": "conversation",
                    "external_target_id": "C_LIVE",
                    "agent_id": replacement_agent["agent"]["id"],
                    "provider_policy": {"interaction_mode": "mention", "reply_mode": "thread"},
                },
            )
            await live.request(
                "PUT",
                base + "/bot/memory-settings",
                json={"expected_version": 1, "memory": {"provider_id": replacement["id"]}},
            )
            changed = True
            await live.request("POST", f"/__live__/cases/{case['case_id']}/release")
            assert failed["agent_id"] != replacement_agent["agent"]["id"]

        try:
            requests = await converse(
                lab,
                journey,
                "slack",
                account,
                target,
                "C_LIVE",
                proof,
                case=case,
                recover=recover if mode == "retry" else None,
            )
            assert document["id"] in memory_index(requests[0])
            assert "REPLACEMENT_AGENT_MUST_NOT_EXECUTE" not in json.dumps(requests)
            if mode != "retry":
                observed = journey.observations(child_case)
                assert document["id"] in memory_index(observed[0]["body"])
        finally:
            if changed:
                current = await live.request("GET", base + "/bot/memory-settings")
                await live.request(
                    "PUT",
                    base + "/bot/memory-settings",
                    json={"expected_version": current["version"], "memory": {"provider_id": provider["id"]}},
                )
            removed = await live.http.delete(path + "/documents/" + document["id"])
            assert removed.status_code == 204
