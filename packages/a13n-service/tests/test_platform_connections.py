"""Business connection authoring excludes secret entry and preserves saved settings."""

import json

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import etag
from a13n_service.resources.connections import platform_tools as tools
from a13n_service.resources.connections.platform_inputs import ConnectionChanges, ConnectionCreation
from a13n_service.resources.connector_providers.platform_tools import TOOLS as CONNECTOR_TOOLS
from a13n_service.resources.providers.platform_tools import TOOLS as PROVIDER_TOOLS
from pydantic import ValidationError

from .test_platform_schedules import context

pytestmark = pytest.mark.anyio


def test_connections_have_no_secret_input_or_implicit_provider_admission():
    assert len(tools.TOOLS) == 10
    assert "configure_connection_access" not in {tool.name for tool in tools.TOOLS}
    assert len(CONNECTOR_TOOLS) == 5 and len(PROVIDER_TOOLS) == 16
    for contract in (*tools.TOOLS, *CONNECTOR_TOOLS, *PROVIDER_TOOLS):
        schema = contract.inputs.model_json_schema()
        assert "request_body" not in schema["properties"]
        for definition in [schema, *schema.get("$defs", {}).values()]:
            assert not {"credential", "client_secret", "setup"} & definition.get("properties", {}).keys()
        if contract.name.startswith("test_"):
            assert contract.effect == "write"
        assert "memory_provider" not in contract.name
    with pytest.raises(ValidationError):
        ConnectionChanges(credential={"token": "secret"})
    with pytest.raises(ValidationError):
        ConnectionCreation(
            type="mcp", name="Unsafe", configuration={"url": "https://example.com/mcp"}, client_secret="secret"
        )


async def test_nonsecret_edits_preserve_credentials_configuration_and_exact_version(service):
    created = await service.client.post(
        f"{service.api}/connections",
        json={
            "type": "mcp",
            "name": "Existing",
            "auth": "bearer",
            "config": {"url": "https://example.com/mcp", "tools": ["read"], "forward_context_headers": False},
            "credential": {"token": "private-credential"},
        },
    )
    assert created.status_code == 201, created.text
    before = created.json()
    ctx = context(service)
    changed = await tools.update_connection(
        ctx,
        connection_id=before["id"],
        expected_version=etag(before["id"], before["version"]),
        body=ConnectionChanges(name="Renamed"),
    )
    assert changed.credential_configured
    assert changed.configuration.url == "https://example.com/mcp"
    assert changed.configuration.tools == ("read",)
    assert changed.configuration.forward_context_headers is False
    assert "private-credential" not in changed.model_dump_json()
    with pytest.raises(ServiceError) as stale:
        await tools.update_connection(
            ctx,
            connection_id=before["id"],
            expected_version=etag(before["id"], before["version"]),
            body=ConnectionChanges(enabled=False),
        )
    assert stale.value.code == "precondition_failed"
    current = await tools.get_connection(ctx, connection_id=changed.id)
    assert current.version == changed.version
    changed_tools = await tools.update_connection(
        ctx,
        connection_id=current.id,
        expected_version=etag(current.id, current.version),
        body=ConnectionChanges(tools=("read", "write")),
    )
    assert changed_tools.credential_configured and changed_tools.configuration.forward_context_headers is False
    assert changed_tools.configuration.tools == ("read", "write")
    pages = await tools.list_connections(ctx)
    assert [item.id for item in pages.items] == [current.id]
    assert "private-credential" not in json.dumps(pages.model_dump(mode="json"))


async def test_connector_creation_reports_trusted_setup_without_partial_resource(service):
    provider = await service.client.post(
        f"{service.api}/connector-providers",
        json={
            "type": "composio",
            "name": "Connector",
            "credential": {"api_key": "private-provider-key"},
        },
    )
    assert provider.status_code == 201, provider.text
    body = ConnectionCreation(
        type="composio",
        name="GitHub",
        auth="account",
        connector_provider_id=provider.json()["id"],
        configuration={"app": "github", "actions": ["GITHUB_CREATE"]},
    )
    result = await tools.create_connection(context(service), body=body)
    assert result.status == "setup_required"
    assert result.connection_id is None and result.version is None
    assert result.proposed_connection == body
    assert (await tools.list_connections(context(service))).items == []
    assert "private-provider-key" not in result.model_dump_json()
