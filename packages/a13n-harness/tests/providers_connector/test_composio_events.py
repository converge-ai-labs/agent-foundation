"""Documented Composio V3 wire contracts, ownership and bounded callback verification."""

import base64
import hmac
import json
from datetime import UTC, datetime

import httpx2
import pytest
from a13n_harness.providers.connector.composio.events import ComposioEvents, verify_event
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.http import ConnectorHttpClient

from .test_connector_adapters import _AllowEndpoint as AllowEndpoint

DEFINITION = {
    "slug": "GITHUB_ISSUE",
    "toolkit": {"slug": "github"},
    "name": "New issue",
    "description": "Issues",
    "version": "20261127_00",
    "config": {"type": "object"},
    "payload": {"type": "object"},
}
WEBHOOK = {
    "id": "wh_one",
    "webhook_url": "https://events.example/callback",
    "secret": "literal-secret",
    "version": "V3",
    "enabled_events": ["composio.trigger.message"],
}


@pytest.mark.anyio
async def test_catalog_and_instance_wire_contracts():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["x-api-key"] == "project-key"
        assert request.url.path.startswith("/api/v3.1/")
        if request.url.path.endswith("triggers_types"):
            assert request.url.params["toolkit_slugs"] == "github"
            return httpx2.Response(200, json={"items": [DEFINITION]})
        if "/triggers_types/" in request.url.path:
            return httpx2.Response(200, json=DEFINITION)
        if request.url.path.endswith("active"):
            assert request.url.params["connected_account_ids"] == "ca_exact"
            assert request.url.params["show_disabled"] == "true"
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "ti_one",
                            "connected_account_id": "ca_exact",
                            "trigger_name": "GITHUB_ISSUE",
                            "user_id": "conn_exact",
                            "trigger_config": {"repo": "r"},
                            "version": "20261127_00",
                            "disabled_at": None,
                            "egress_url": None,
                        }
                    ]
                },
            )
        body = json.loads(request.content)
        assert body == {
            "connected_account_id": "ca_exact",
            "user_id": "conn_exact",
            "trigger_config": {"repo": "r"},
            "toolkit_versions": {"github": "20261127_00"},
        }
        return httpx2.Response(200, json={"trigger_id": "ti_one"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        events = ComposioEvents(ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "project-key")
        definition = await events.event_type("github", "GITHUB_ISSUE")
        assert (await events.event_types("github"))[0] == definition
        assert (await events.instances("ca_exact", definition.key))[0].user_id == "conn_exact"
        assert await events.upsert(definition, "ca_exact", "conn_exact", {"repo": "r"}) == "ti_one"
    assert len(calls) == 4


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [{"webhook_url": "https://another.example"}, {"id": "wh_other"}])
async def test_shared_webhook_refuses_other_owner_or_changed_project_without_write(changed):
    def respond(request):
        assert request.method == "GET"
        return httpx2.Response(200, json={"items": [{**WEBHOOK, **changed}]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        events = ComposioEvents(ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "key")
        with pytest.raises(ConnectorProviderError, match="webhook_owned_elsewhere"):
            await events.configure_webhook(WEBHOOK["webhook_url"], "wh_one", rotate=True)


@pytest.mark.anyio
async def test_webhook_creation_rotation_and_idempotent_cleanup():
    stored = []
    writes = []

    def respond(request):
        if request.method == "GET":
            return httpx2.Response(200, json={"items": stored})
        writes.append(request)
        if request.url.path.endswith("rotate_secret"):
            stored[0] = {**WEBHOOK, "secret": "rotated"}
            return httpx2.Response(200, json={"id": "wh_one", "secret": "rotated"})
        if request.method == "DELETE":
            return httpx2.Response(204 if len(writes) == 3 else 404)
        assert json.loads(request.content) == {
            "webhook_url": WEBHOOK["webhook_url"],
            "version": "V3",
            "enabled_events": ["composio.trigger.message"],
        }
        stored.append(WEBHOOK)
        return httpx2.Response(201, json=WEBHOOK)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        events = ComposioEvents(ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "key")
        first = await events.configure_webhook(WEBHOOK["webhook_url"], None, rotate=False)
        second = await events.configure_webhook(WEBHOOK["webhook_url"], first.id, rotate=True)
        recovered = await events.configure_webhook(WEBHOOK["webhook_url"], first.id, rotate=False)
        assert second.secret.get_secret_value() == recovered.secret.get_secret_value() == "rotated"
        await events.transition("ti_one", "retired")
        await events.transition("ti_one", "retired")
    assert len(writes) == 4


@pytest.mark.parametrize("mutation", ["body", "identity", "stale", "future", "version", "naive"])
def test_signature_authenticates_exact_bytes_and_bounded_timestamp(mutation):
    now = datetime(2026, 11, 27, tzinfo=UTC)
    envelope = {
        "id": "msg_one",
        "type": "composio.trigger.message",
        "timestamp": now.isoformat(),
        "metadata": {
            "trigger_id": "ti_one",
            "connected_account_id": "ca_one",
            "user_id": "conn_one",
            "trigger_slug": "GITHUB_ISSUE",
        },
        "data": {"number": 1},
    }
    if mutation == "naive":
        envelope["timestamp"] = "2026-11-27T00:00:00"
    raw = json.dumps(envelope).encode()
    timestamp = str(int(now.timestamp()) + ({"stale": -301, "future": 301}.get(mutation, 0)))
    signature = base64.b64encode(
        hmac.digest(b"literal-secret", b"msg_one." + timestamp.encode() + b"." + raw, "sha256")
    ).decode()
    headers = {"webhook-id": "msg_one", "webhook-timestamp": timestamp, "webhook-signature": "v1," + signature}
    if mutation not in {"stale", "future", "naive"}:
        assert verify_event(raw, headers, "literal-secret", now=now).account_id == "ca_one"
    if mutation == "body":
        raw += b" "
    if mutation == "identity":
        headers["webhook-id"] = "msg_other"
    if mutation == "version":
        headers["webhook-signature"] = "v2," + signature
    with pytest.raises(ConnectorProviderError, match="invalid_event_signature_or_payload"):
        verify_event(raw, headers, "literal-secret", now=now)
