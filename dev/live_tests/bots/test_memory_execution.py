"""Signed Slack/Feishu HTTP ingress through separate roles, real Mem0, and native reply."""

import json
import os
from uuid import uuid4

import pytest

from ..infrastructure.management_support import ManagementJourney
from ..infrastructure.round_two_lab import open_lab
from .journey import converse
from .model import memory_index
from .resources import setup

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("platform", ["slack", "lark"])
async def test_signed_ingress_reads_native_memory_and_records_a_single_thread_reply(request, platform):
    if not request.config.getoption("--live-management"):
        pytest.skip("Opt in with --live-management for owned Control/Connectivity/Worker processes")
    if not os.environ.get("TEST_MEM0_OSS_URL") or not os.environ.get("TEST_MEM0_OSS_API_KEY"):
        pytest.skip("Set TEST_MEM0_OSS_URL/API_KEY to an explicitly configured native OSS test server")
    async with open_lab(suite="management", local_connectors=False, bot_memory=True) as lab:
        journey, live = ManagementJourney(lab), lab.client
        conversation = "C_LIVE" if platform == "slack" else "oc_live"
        account, target, _scope, path, document, proof, agent, _ = await setup(journey, platform)
        agent_id = agent["agent"]["id"]
        base = f"/api/v1/application-accounts/{account['id']}"
        source_deleted = False
        other_document = None
        other_path = None
        try:
            # Same Bot and native Provider, distinct private conversation. Its
            # random body and reference must never reach this conversation's model.
            other_conversation = "C_OTHER" if platform == "slack" else "oc_other"
            other_target = await journey.post(
                base + "/targets", {"target_kind": "conversation", "external_target_id": other_conversation}
            )
            other_check = await journey.post(
                base + "/bot/checks",
                {"expected_version": account["version"], "conversation_id": other_conversation},
                expected=200,
            )
            assert other_check["error_code"] is None and other_check["conversation"]["audience"] == "private"
            other_scope = await journey.post(
                base + "/memory-scopes", {"external_conversation_id": other_conversation}, expected=200
            )
            other_path = f"{base}/memory-scopes/{other_scope['id']}"
            other_proof = "OTHER-PRIVATE-GROUP-" + uuid4().hex
            other_document = await journey.post(
                other_path + "/documents", {"title": "Private team note", "text": other_proof}
            )
            requests = await converse(lab, journey, platform, account, target, conversation, proof)
            assert document["id"] in memory_index(requests[0]) and proof not in json.dumps(requests[0])
            assert proof in json.dumps(requests[1])
            assert other_proof not in json.dumps(requests) and other_document["id"] not in json.dumps(requests)

            # Publish a reviewed body distinct from the private original.
            shared_proof = "APPROVED-SHARED-BODY-" + uuid4().hex
            publication = await journey.post(
                path + f"/documents/{document['id']}/publications",
                {"title": "Shared release facts", "text": shared_proof, "recipient_scope_ids": [other_scope["id"]]},
            )
            assert publication["id"] != document["id"]
            removed = await live.http.delete(other_path + "/documents/" + other_document["id"])
            assert removed.status_code == 204
            other_document = None
            other_target = await live.request(
                "PUT",
                base + f"/targets/{other_target['id']}",
                json={
                    "expected_version": other_target["version"],
                    "target_kind": "conversation",
                    "external_target_id": other_conversation,
                    "agent_id": agent_id,
                    "provider_policy": {"interaction_mode": "mention", "reply_mode": "thread"},
                },
            )
            shared = await converse(lab, journey, platform, account, other_target, other_conversation, shared_proof)
            assert publication["id"] in memory_index(shared[0]) and document["id"] not in memory_index(shared[0])
            assert shared_proof not in json.dumps(shared[0]) and shared_proof in json.dumps(shared[1])
            assert proof not in json.dumps(shared) and other_proof not in json.dumps(shared)

            if not source_deleted:
                deleted = await live.http.delete(path + "/documents/" + document["id"])
                assert deleted.status_code == 204
            source_deleted = True
            revoked = await converse(
                lab,
                journey,
                platform,
                account,
                other_target,
                other_conversation,
                "MEMORY_NOT_AVAILABLE",
                stale_reference="memory://" + publication["id"],
            )
            assert publication["id"] not in memory_index(revoked[0])
            assert shared_proof not in json.dumps(revoked) and proof not in json.dumps(revoked)
        finally:
            if other_document is not None:
                removed = await live.http.delete(other_path + "/documents/" + other_document["id"])
                assert removed.status_code == 204
            if not source_deleted:
                deleted = await live.http.delete(path + "/documents/" + document["id"])
                assert deleted.status_code == 204
