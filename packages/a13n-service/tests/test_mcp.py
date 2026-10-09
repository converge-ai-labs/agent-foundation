"""The MCP boundary uses real Service credentials, routes, preconditions and audit."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_service.api_tools import api_tools, tool_name
from a13n_service.app import build_app
from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session
from a13n_service.settings import Settings
from a13n_service.tenancy.access import Authenticated
from a13n_service.tenancy.authenticate import LocalAuthenticator
from a13n_service.tenancy.requests import Actor
from fastapi import APIRouter, Request, Response
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from sqlalchemy import select

from .mcp_support import HEADERS, MCP, call, client, key, rpc

pytestmark = pytest.mark.anyio


async def test_management_fidelity_and_audit(service: SimpleNamespace) -> None:
    credential = await key(service)
    async with client(service, credential) as http:
        discovery = await rpc(http, "tools/list")
        assert discovery.status_code == 200, discovery.text
        assert discovery.json()["result"]["tools"]
        created = await call(http, service, "POST", "/memories", request_body={"name": "notes", "guide": "custom"})
        assert created["status"] == 201
        memory_id = created["body"]["id"]
        assert created["headers"]["etag"]
        assert created["headers"]["x-request-id"]
        args = {"memory_id": memory_id}
        missing = await call(http, service, "PATCH", "/memories/{memory_id}", **args, request_body={})
        assert missing["status"] == 428
        assert missing["body"]["error"]["code"] == "precondition_required"
        unchanged = await call(
            http,
            service,
            "PATCH",
            "/memories/{memory_id}",
            **args,
            **{"If-Match": created["headers"]["etag"]},
            request_body={},
        )
        assert unchanged["status"] == 200 and unchanged["body"]["guide"] == "custom"
        cleared = await call(
            http,
            service,
            "PATCH",
            "/memories/{memory_id}",
            **args,
            **{"If-Match": unchanged["headers"]["etag"]},
            request_body={"guide": None},
        )
        assert cleared["status"] == 200 and cleared["body"]["guide"] is None
        stale = await call(
            http,
            service,
            "PATCH",
            "/memories/{memory_id}",
            **args,
            **{"If-Match": created["headers"]["etag"]},
            request_body={"guide": "stale"},
        )
        assert stale["status"] == 412 and stale["body"]["error"]["code"] == "precondition_failed"
        assert stale["body"]["error"]["request_id"] == stale["headers"]["x-request-id"]
        listing = await call(http, service, "GET", "/memories", limit=1)
        assert listing["body"]["items"][0]["id"] == memory_id
        deleted = await call(
            http, service, "DELETE", "/memories/{memory_id}", **args, **{"If-Match": cleared["headers"]["etag"]}
        )
        assert deleted["status"] == 204 and deleted["body"] is None
    async with short_session(service.runtime.storage) as session:
        events = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == memory_id))).all()
    assert [event.action for event in events] == ["memory.create", "memory.update", "memory.delete"]
    assert all(event.actor_id == service.tenant.principal_id for event in events)


async def test_credentials_discovery_confinement_and_concurrency(service: SimpleNamespace) -> None:
    # A valid browser session is not an MCP credential.
    cookie_only = await service.client.post(
        MCP, headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert cookie_only.status_code == 401
    a = await key(service)
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    other_id = other.json()["id"]
    b = await key(service, other_id)
    async with client(service, a) as first, client(service, b) as second:
        results = await asyncio.gather(
            *[
                call(first if i % 2 else second, service, "POST", "/memories", request_body={"name": f"notes-{i}"})
                for i in range(8)
            ]
        )
        for i, result in enumerate(results):
            assert result["status"] == 201
            assert result["body"]["workspace_id"] == (service.tenant.workspace_id if i % 2 else other_id)
        first.headers["x-workspace-id"] = other_id
        assert (await rpc(first, "tools/list")).status_code == 403
        del first.headers["x-workspace-id"]
        foreign = await call(first, service, "GET", "/memories/{memory_id}", memory_id=results[0]["body"]["id"])
        assert foreign["status"] == 404
        revoked = await service.client.delete(
            f"/api/v1/users/me/keys/{a['key']['id']}", headers={"If-Match": f'"{a["key"]["id"]}:{a["key"]["version"]}"'}
        )
        assert revoked.status_code == 200, revoked.text
        assert (await rpc(first, "tools/list")).status_code == 401
        assert (await rpc(second, "tools/list")).status_code == 200
        second.headers["origin"] = "https://attacker.test"
        assert (await rpc(second, "tools/list")).status_code == 403
        del second.headers["origin"]
        second.headers["host"] = "attacker.test"
        assert (await rpc(second, "tools/list")).status_code == 421


async def test_real_client_and_two_api_process_lifespans(service: SimpleNamespace, listen: Any) -> None:
    credential = await key(service)
    second = build_app(role="all", settings=service.runtime.settings)
    async with second.router.lifespan_context(second):
        assert second.state.runtime is not service.runtime
        async with listen(service.app) as first_url, listen(second) as second_url:
            headers = {"authorization": "Bearer " + credential["secret"]}
            async with Client(StreamableHttpTransport(first_url + MCP, headers=headers)) as first:
                tools = await first.list_tools()
                assert len(tools) == 121
                operation = service.app.openapi()["paths"]["/api/v1/memories"]["post"]
                created = await first.call_tool(
                    tool_name(operation["operationId"]), {"request_body": {"name": "shared"}}
                )
                assert created.structured_content["status"] == 201
                memory_id = created.structured_content["body"]["id"]
                # A second process requires no affinity, cached credential or protocol session.
                async with Client(StreamableHttpTransport(second_url + MCP, headers=headers)) as other:
                    operation = second.openapi()["paths"]["/api/v1/memories/{memory_id}"]["get"]
                    read = await other.call_tool(tool_name(operation["operationId"]), {"memory_id": memory_id})
                    assert read.structured_content["body"]["name"] == "shared"
                    docs = await other.call_tool("search_documents", {"query": "upload", "limit": 1})
                    assert docs.structured_content["results"]
    # Shutting down another process must not stop this one's MCP manager or Runtime.
    async with client(service, credential) as http:
        assert (await rpc(http, "tools/list")).status_code == 200


class ExtensionAuthenticator(LocalAuthenticator):
    """A Distribution-owned bearer format and credential kind, without browser cookie fallback."""

    def __init__(self) -> None:
        self.credential: Authenticated | None = None
        self.cookies: list[str] = []

    async def authenticate(self, request: Request, response: Response) -> Authenticated | None:
        if request.headers.get("authorization") == "Bearer extension-key":
            self.cookies.append(request.headers.get("cookie", ""))
            return self.credential
        result = await super().authenticate(request, response)
        if result is not None and result.principal.confinement is not None:
            self.credential = replace(result, kind="distribution-key")
        return result


async def test_distribution_authentication_and_explicit_operation_admission(serve: Any) -> None:
    router = APIRouter(prefix="/api/v1/extension")

    @router.get("/selected", operation_id="extension_selected", openapi_extra={"x-a13n-mcp": True})
    async def selected(actor: Actor) -> dict[str, str]:
        return {"principal": actor.id}

    @router.get("/unselected", operation_id="extension_unselected")
    async def unselected(actor: Actor) -> dict[str, str]:
        return {"principal": actor.id}

    auth = ExtensionAuthenticator()
    async with serve(
        distribution=OSS.extend(Distribution(name="extension", routers=(router,), authenticator=auth))
    ) as service:
        credential = await key(service)
        async with client(service, credential) as http:
            assert (await rpc(http, "tools/list")).status_code == 200
            http.headers["authorization"] = "Bearer extension-key"
            http.headers["cookie"] = "session=must-not-reach-authenticator"
            tools = (await rpc(http, "tools/list")).json()["result"]["tools"]
            assert "extension_selected" in {tool["name"] for tool in tools}
            assert "extension_unselected" not in {tool["name"] for tool in tools}
            result = await call(http, service, "GET", "/extension/selected")
            assert result["body"] == {"principal": service.tenant.principal_id}
            assert auth.cookies and not any(auth.cookies)
            assert auth.credential is not None
            auth.credential = replace(auth.credential, principal=replace(auth.credential.principal, confinement=None))
            assert (await rpc(http, "tools/list")).status_code == 401
            auth.credential = None
            assert (await rpc(http, "tools/list")).status_code == 401


def test_worker_has_no_mcp_and_unmarked_routes_are_absent() -> None:
    settings = Settings()
    worker = build_app(role="worker", settings=settings)
    assert not any(route.path.startswith("/api/v1") for route in worker.routes)
    app = build_app(role="control", settings=settings)
    schema = app.openapi()
    selected = {
        (path, method) for path, item in schema["paths"].items() for method, op in item.items() if op.get("x-a13n-mcp")
    }
    assert len(selected) == 120
    assert ("/api/v1/runs/{run_id}/attempts/{attempt_id}/trace", "get") in selected
    assert ("/api/v1/skills", "post") in selected
    assert not any(
        "/auth" in path or "/uploads" in path or "/organizations" in path or "/users" in path for path, _ in selected
    )
    assert not any(path.endswith("/content") or path.endswith("/authorize") for path, _ in selected)
    tools = api_tools(
        app, schema, settings.server.public_origin, [route for router in OSS.routers for route in router.routes]
    )
    assert len(tools) == len(selected) == len({tool.name for tool in tools})
    assert all(len(tool.name) <= 64 for tool in tools)
    assert all("X-Workspace-ID" not in tool.parameters["properties"] for tool in tools)
