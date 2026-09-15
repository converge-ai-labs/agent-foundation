import asyncio
import json

import httpx2
import pytest

from a13n import (
    AgentConfig,
    AgentRunOverride,
    ApiError,
    Client,
    CreateWebProviderRequest,
    ProtocolError,
    SearchToolConfiguration,
    ToolSelection,
    ToolsetSelection,
    TransportError,
    UpdateWebProviderRequest,
    WebProviderScope,
)

SCOPE = WebProviderScope("workspace", "ws_test")
PROVIDER = {
    "id": "wprov_test",
    "organization_id": "org_test",
    "workspace_id": "ws_test",
    "type": "brave",
    "name": "Research",
    "configuration": {},
    "enabled": True,
    "credential_configured": True,
    "created_at": "2026-09-09T00:00:00Z",
    "updated_at": "2026-09-09T00:00:00Z",
    "created_by": {"principal_type": "user", "principal_id": "user_test"},
    "updated_by": {"principal_type": "user", "principal_id": "user_test"},
}


def test_configuration_preserves_toolset_replacement_and_unrelated_fields():
    assert AgentRunOverride().to_wire() == {}
    value = AgentRunOverride(
        toolsets={
            "web": ToolsetSelection(
                enabled=True,
                tools={
                    "search": ToolSelection(
                        config=SearchToolConfiguration(provider_id="wprov_test").model_dump(
                            mode="json", exclude_none=True
                        )
                    )
                },
            )
        },
        instructions="retain",
    )
    assert value.to_wire() == {
        "toolsets": {
            "web": {
                "enabled": True,
                "tools": {
                    "search": {
                        "config": {
                            "provider_id": "wprov_test",
                            "max_results": 5,
                            "allow_domains": [],
                            "deny_domains": [],
                        },
                    }
                },
            }
        },
        "instructions": "retain",
    }
    assert AgentConfig.model_validate({"model": {"model_key": "research"}}).to_wire() == {
        "model": {"model_key": "research"}
    }


def test_scoped_crud_etags_and_credentials_are_wire_only():
    async def run():
        requests = []

        def respond(request):
            requests.append(request)
            assert request.headers["Authorization"] == "Bearer service-token"
            return httpx2.Response(
                200,
                json={**PROVIDER, "credential": "unexpected-secret"},
                headers={"ETag": '"v1"', "X-Request-ID": "req_test"},
            )

        creation = CreateWebProviderRequest(
            type="external_web",
            name="Research",
            credential={
                "api_key": "test-secret",
                "nested": {"client_secret": "nested-secret", "tenant": None},
            },
        )
        assert "secret" not in repr(creation) + creation.model_dump_json()
        async with Client(
            "https://service.example/prefix", "service-token", transport=httpx2.MockTransport(respond)
        ) as client:
            result = await client.create_web_provider(SCOPE, creation)
            assert result.etag == '"v1"' and result.request_id == "req_test"
            assert "unexpected-secret" not in repr(result) + result.model_dump_json()
            assert json.loads(requests[-1].content) == {
                "type": "external_web",
                "name": "Research",
                "credential": {
                    "api_key": "test-secret",
                    "nested": {"client_secret": "nested-secret", "tenant": None},
                },
            }
            await client.update_web_provider(
                SCOPE,
                result.value.id,
                result.etag,
                UpdateWebProviderRequest(
                    enabled=False,
                    credential={"token": {"primary": "rotated-secret"}},
                ),
            )
            assert requests[-1].headers["If-Match"] == '"v1"'
            assert json.loads(requests[-1].content) == {
                "enabled": False,
                "credential": {"token": {"primary": "rotated-secret"}},
            }
            await client.web_provider(WebProviderScope("organization", "org_test"), result.value.id)
            assert requests[-1].url.path == "/prefix/api/v1/organizations/org_test/web-providers/wprov_test"

    asyncio.run(run())


def test_collections_types_references_and_explicit_probe():
    async def run():
        paths = []

        def respond(request):
            paths.append(request.url.path)
            if request.url.path.endswith("/test"):
                assert json.loads(request.content) == {}
                return httpx2.Response(200, json={"success": True, "code": None, "checked_at": "2026-09-09T00:00:00Z"})
            if request.url.path.endswith("/references"):
                return httpx2.Response(
                    200,
                    json={
                        "items": [
                            {
                                "agent_id": "agent_test",
                                "agent_revision_id": "rev_test",
                                "version": 1,
                                "is_current": True,
                            }
                        ]
                    },
                )
            if "web-provider-types" in request.url.path:
                definition = {
                    "type": "brave",
                    "display_name": "Brave",
                    "credential_schema": {"writeOnly": True},
                    "configuration_schema": {},
                    "setup_url": "https://example.com/",
                    "operations": ["search"],
                }
                return httpx2.Response(
                    200, json=definition if request.url.path.endswith("/brave") else {"items": [definition]}
                )
            assert request.url.params["cursor"] == "next"
            return httpx2.Response(200, json={"items": [PROVIDER], "next_cursor": "later"})

        async with Client("https://service.example", "token", transport=httpx2.MockTransport(respond)) as client:
            assert (await client.web_provider_types()).items[0].type == "brave"
            assert (await client.web_provider_type("brave")).type == "brave"
            assert (await client.web_providers(SCOPE, cursor="next")).next_cursor == "later"
            assert (await client.web_provider_references(SCOPE, "wprov_test")).items[0].is_current
            assert (await client.test_web_provider(SCOPE, "wprov_test")).success
        assert len(paths) == 5

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["create", "update", "test"])
def test_uncertain_mutations_never_replay_or_expose_transport_errors(operation):
    async def run():
        calls = 0

        def respond(request):
            nonlocal calls
            calls += 1
            raise httpx2.ReadError("sensitive transport detail", request=request)

        async with Client("https://service.example", "token", transport=httpx2.MockTransport(respond)) as client:
            with pytest.raises(TransportError) as error:
                if operation == "create":
                    await client.create_web_provider(
                        SCOPE,
                        CreateWebProviderRequest(
                            type="external_web", name="Research", credential={"nested": {"token": "test-secret"}}
                        ),
                    )
                elif operation == "update":
                    await client.update_web_provider(
                        SCOPE,
                        "wprov_test",
                        '"v1"',
                        UpdateWebProviderRequest(credential={"nested": {"token": "test-secret"}}),
                    )
                else:
                    await client.test_web_provider(SCOPE, "wprov_test")
            assert "sensitive" not in str(error.value)
            assert calls == 1

    asyncio.run(run())


def test_safe_errors_bounded_responses_redirects_and_close():
    async def run():
        for status, payload, expected in [
            (412, {"error": {"code": "precondition_failed", "message": "Changed", "request_id": "req_test"}}, ApiError),
            (200, "x" * 1_048_577, ProtocolError),
            (302, {}, ApiError),
        ]:

            def respond(request, status=status, payload=payload):
                return httpx2.Response(status, json=payload, headers={"Location": "https://outside.example"})

            async with Client("https://service.example", "token", transport=httpx2.MockTransport(respond)) as client:
                with pytest.raises(expected):
                    await client.web_provider(SCOPE, "wprov_test")
            with pytest.raises(TransportError):
                await client.web_provider(SCOPE, "wprov_test")

    asyncio.run(run())


def test_close_cancels_inflight_request_without_replay():
    async def run():
        entered = asyncio.Event()

        async def respond(request):
            entered.set()
            await asyncio.Event().wait()

        client = Client("https://service.example", "token", transport=httpx2.MockTransport(respond))
        task = asyncio.create_task(client.test_web_provider(SCOPE, "wprov_test"))
        await entered.wait()
        await client.aclose()
        assert task.cancelled()

    asyncio.run(run())


def test_workspace_binding_uses_context_once_and_shares_shutdown():
    async def run():
        paths = []

        def respond(request):
            paths.append(request.url.path)
            if request.url.path.endswith("/auth/context"):
                return httpx2.Response(200, json={"workspace_id": "ws_test", "workspace_key": "renamed"})
            return httpx2.Response(200, json={"items": [PROVIDER]})

        client = Client("https://service.example/prefix", "token", transport=httpx2.MockTransport(respond))
        workspace = await client.workspace()
        for _ in range(2):
            assert (await workspace.web_providers()).items[0].id == "wprov_test"
        assert paths == ["/prefix/api/v1/auth/context"] + ["/prefix/api/v1/workspaces/ws_test/web-providers"] * 2
        await client.aclose()
        with pytest.raises(TransportError):
            await workspace.web_providers()

    asyncio.run(run())


@pytest.mark.parametrize("context", [{}, {"workspace_id": None}, {"workspace_id": ""}])
def test_workspace_binding_requires_workspace_credential(context):
    async def run():
        async with Client(
            "https://service.example",
            "token",
            transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=context)),
        ) as client:
            with pytest.raises(ValueError, match="Workspace-bound"):
                await client.workspace()

    asyncio.run(run())
