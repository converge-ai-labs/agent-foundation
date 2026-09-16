from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.models.domain import (
    ModelDeclarations,
    ModelPricing,
)
from a13n_service.settings import Settings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request

NOW = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"


async def authenticate(request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


async def successful_model_test(**_: object) -> None:
    return None


def settings(tmp_path: Path, database_path: Path) -> Settings:
    return Settings(
        iam={"initial_admin_email": "admin@example.com"},
        database={"backend": "sqlite", "sqlite_path": database_path},
        redis={"backend": "memory"},
        objects={"backend": "local", "local_root": tmp_path / "objects"},
        filesystem={"root": tmp_path / "files"},
        models={"resolve_dns_on_save": False},
        secrets={
            "master_key_base64": b64encode(b"0123456789abcdef0123456789abcdef").decode(),
            "encryption_key_id": "model-management-test-key",
        },
        connectivity={"public_origin": "http://127.0.0.1", "http_origins": ("http://127.0.0.1",)},
    )


async def seed_database(configuration: Settings) -> None:
    engine = create_sql_engine(configuration.database_config())
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, key="test", name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                key="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            UserRecord(
                id=USER_ID,
                email="builder@example.com",
                normalized_email="builder@example.com",
                name="Builder",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_org1234567890abcd",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_ws1234567890abcde",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="builder",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    await engine.dispose()


@pytest.fixture
async def api_client(
    tmp_path: Path,
    service_sqlite_database: Path,
    model_catalog,
) -> AsyncIterator[httpx2.AsyncClient]:
    configuration = settings(tmp_path, service_sqlite_database)
    await seed_database(configuration)
    app = create_app(
        configuration,
        components=Components(
            request_authenticator=authenticate,
            model_connection_tester=successful_model_test,
            model_catalog=model_catalog,
        ),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def create_provider(api_client: httpx2.AsyncClient, name: str = "OpenAI Primary") -> dict[str, object]:
    response = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers",
        json={"type": "openai", "name": name, "credential": "sk-secret"},
    )
    assert response.status_code == 201
    assert "sk-secret" not in response.text
    return response.json()


@pytest.mark.anyio
async def test_provider_type_and_multiple_provider_http_lifecycle(api_client: httpx2.AsyncClient) -> None:
    definitions = await api_client.get("/api/v1/model-provider-types")
    first = await create_provider(api_client, "OpenAI Production")
    second = await create_provider(api_client, "OpenAI Personal")

    assert definitions.status_code == 200
    assert {item["type"] for item in definitions.json()["items"]} >= {"openai", "openrouter", "ollama"}
    openai = next(item for item in definitions.json()["items"] if item["type"] == "openai")
    assert set(openai["settings_schemas"]) == {"openai.responses", "openai.chat_completions"}
    assert "temperature" in openai["settings_schemas"]["openai.responses"]["properties"]
    assert first["type"] == second["type"] == "openai"
    assert first["id"] != second["id"]
    assert first["credential_configured"] and "credential" not in first

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers?provider_type=openai")
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} == {first["id"], second["id"]}


@pytest.mark.anyio
async def test_model_http_lifecycle_has_no_revision_or_default_api(api_client: httpx2.AsyncClient) -> None:
    provider = await create_provider(api_client)
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models",
        json={
            "key": "support/main",
            "provider_id": provider["id"],
            "name": "Support",
            "upstream_model": "gpt-current",
            "model_api": "openai.responses",
        },
    )
    assert created.status_code == 201
    model = created.json()
    assert model["key"] == "support/main"
    assert model["provider_id"] == provider["id"]
    assert "version" not in model and "current_revision_id" not in model
    assert "default_model_api" not in model
    model_url = f"/api/v1/workspaces/{WORKSPACE_ID}/models/{model['id']}"

    fetched = await api_client.get(model_url)
    assert fetched.status_code == 200
    assert fetched.json() == model

    stale = await api_client.patch(model_url, json={"enabled": False}, headers={"If-Match": '"stale"'})
    assert stale.status_code == 412
    patched = await api_client.patch(
        model_url,
        json={"upstream_model": "gpt-new"},
        headers={"If-Match": fetched.headers["etag"]},
    )
    assert patched.status_code == 200
    assert patched.json()["upstream_model"] == "gpt-new"
    assert patched.json()["key"] == model["key"]
    assert patched.json()["provider_id"] == provider["id"]

    tested = await api_client.post(f"{model_url}/test", json={})
    assert tested.status_code == 200
    assert tested.json()["success"]
    assert (await api_client.get(f"{model_url}/revisions")).status_code == 404


@pytest.mark.anyio
async def test_unknown_fields_use_shared_safe_error(api_client: httpx2.AsyncClient) -> None:
    response = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers",
        json={"type": "openai", "name": "OpenAI", "credential": "must-not-leak", "api_key": "must-not-leak"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "must-not-leak" not in response.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


@pytest.mark.anyio
async def test_missing_authenticator_returns_401(
    tmp_path: Path,
    service_sqlite_database: Path,
) -> None:
    configuration = settings(tmp_path, service_sqlite_database)
    await seed_database(configuration)
    app = create_app(configuration)
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/model-provider-types")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_manual_model_ids_do_not_require_discovery(api_client):
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers",
        json={
            "type": "aws_bedrock",
            "name": "Bedrock",
            "configuration": {"region": "us-east-1"},
            "credential": '{"aws_access_key_id":"test","aws_secret_access_key":"test"}',
        },
    )
    assert created.status_code == 201
    provider_id = created.json()["id"]
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers/{provider_id}"
    unsupported = await api_client.post(f"{url}/discover-models", json={})
    assert unsupported.status_code == 409
    assert "model_discovery_unsupported" in unsupported.text
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models",
        json={
            "key": "manual",
            "provider_id": provider_id,
            "name": "Manual",
            "upstream_model": "new/unlisted-deployment",
            "model_api": "bedrock.converse",
            "declarations": {"context_window_tokens": 200000},
        },
    )
    assert created.status_code == 201
    assert created.json()["declarations"] == {
        "supports_tools": None,
        "thinking_efforts": [],
        "capabilities": [],
        "context_window_tokens": 200000,
        "max_output_tokens": None,
        "structured_output": None,
        "pricing": None,
    }


@pytest.mark.anyio
async def test_model_test_rejects_removed_api_selector(api_client):
    response = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models/mdl_1234567890abcdef/test", json={"model_api": "openai.responses"}
    )
    assert response.status_code in {400, 422}


@pytest.mark.anyio
async def test_model_capabilities_are_readonly_discovery_information(api_client):
    provider = (
        await api_client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers",
            json={"type": "openai", "name": "OpenAI", "credential": "secret"},
        )
    ).json()
    payload = {
        "key": "primary",
        "provider_id": provider["id"],
        "name": "Primary",
        "upstream_model": "gpt-next",
        "model_api": "openai.responses",
        "settings": {"max_tokens": 8000},
    }
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/models"
    for field in ("profile", "limits"):
        rejected = await api_client.post(url, json=payload | {field: {}})
        assert rejected.status_code == 400
    created = await api_client.post(url, json=payload)
    assert created.status_code == 201
    assert {"profile", "limits"}.isdisjoint(created.json())
    assert created.json()["settings"] == {"max_tokens": 8000}
    assert created.json()["declarations"] == {
        "supports_tools": None,
        "thinking_efforts": [],
        "capabilities": [],
        "context_window_tokens": None,
        "max_output_tokens": None,
        "structured_output": None,
        "pricing": None,
    }
    for field in ("profile", "limits"):
        rejected = await api_client.patch(
            f"{url}/{created.json()['id']}", json={field: {}}, headers={"If-Match": created.headers["etag"]}
        )
        assert rejected.status_code == 400


@pytest.mark.anyio
async def test_catalog_suggestions_prefill_omitted_declarations_but_explicit_clears_win(api_client, model_catalog):
    provider = await create_provider(api_client)
    suggested = ModelDeclarations(
        thinking_efforts=("low", "high"),
        capabilities=("image_understanding",),
        context_window_tokens=400_000,
        max_output_tokens=128_000,
        structured_output=True,
        pricing=ModelPricing(input=1.25, output=10, cache_read=0.125, cache_write=2.5),
    )
    model_catalog.results[("openai", "openai:gpt-5")] = suggested
    candidates = await api_client.get("/api/v1/base-models")
    assert candidates.status_code == 200
    assert any(item["base_model"] == "openai:gpt-5" for item in candidates.json()["items"])
    suggestion = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-catalog/suggestions",
        json={"provider_id": provider["id"], "upstream_model": "gpt-5"},
    )
    assert suggestion.status_code == 200
    assert suggestion.json()["source"] == "exact"
    assert suggestion.json()["items"][0]["base_model"] == "openai:gpt-5"
    assert suggestion.json()["items"][0]["model_api"] == "openai.responses"
    assert suggestion.json()["items"][0]["model_api_label"] == "OpenAI Responses"
    assert suggestion.json()["items"][0]["declarations"] == suggested.model_dump(mode="json")

    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models",
        json={
            "key": "catalog-defaults",
            "provider_id": provider["id"],
            "name": "Catalog defaults",
            "upstream_model": "gpt-5",
            "model_api": "openai.responses",
            "declarations": {
                "thinking_efforts": [],
                "context_window_tokens": None,
                "structured_output": False,
                "pricing": {"input": 0, "cache_read": None},
            },
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["base_model"] == "openai:gpt-5"
    assert created.json()["declarations"] == {
        "supports_tools": None,
        "thinking_efforts": [],
        "capabilities": ["image_understanding"],
        "context_window_tokens": None,
        "max_output_tokens": 128_000,
        "structured_output": False,
        "pricing": {"input": 0.0, "output": 10.0, "cache_read": None, "cache_write": 2.5},
    }

    replacement = ModelDeclarations(
        context_window_tokens=1_000_000,
        max_output_tokens=250_000,
        structured_output=False,
        pricing=ModelPricing(input=99, output=199),
    )
    model_catalog.results[("openai", "openai:gpt-6-astra")] = replacement
    fresh_suggestion = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-catalog/suggestions",
        json={"provider_id": provider["id"], "upstream_model": "gpt-6-astra"},
    )
    assert fresh_suggestion.status_code == 200
    assert fresh_suggestion.json()["items"][0]["declarations"] == replacement.model_dump(mode="json")

    updated = await api_client.patch(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models/{created.json()['id']}",
        json={"upstream_model": "gpt-6-astra"},
        headers={"If-Match": created.headers["etag"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["upstream_model"] == "gpt-6-astra"
    assert updated.json()["base_model"] == "openai:gpt-5"
    assert updated.json()["declarations"] == created.json()["declarations"]


@pytest.mark.anyio
async def test_base_model_explicit_override_and_null_suppression(api_client):
    provider = await create_provider(api_client)
    collection_url = f"/api/v1/workspaces/{WORKSPACE_ID}/models"

    overridden = await api_client.post(
        collection_url,
        json={
            "key": "chat-override",
            "provider_id": provider["id"],
            "name": "Chat override",
            "upstream_model": "relay-gpt-5",
            "base_model": "openai:gpt-5",
            "model_api": "openai.chat_completions",
        },
    )
    assert overridden.status_code == 201, overridden.text
    assert overridden.json()["base_model"] == "openai:gpt-5"
    assert overridden.json()["model_api"] == "openai.chat_completions"

    suppressed = await api_client.post(
        collection_url,
        json={
            "key": "no-base",
            "provider_id": provider["id"],
            "name": "No base",
            "upstream_model": "gpt-5",
            "base_model": None,
            "model_api": "openai.responses",
        },
    )
    assert suppressed.status_code == 201, suppressed.text
    assert suppressed.json()["base_model"] is None

    invalid = await api_client.post(
        collection_url,
        json={
            "key": "invalid-base",
            "provider_id": provider["id"],
            "name": "Invalid base",
            "upstream_model": "gpt-5",
            "base_model": "openai:not-installed",
            "model_api": "openai.responses",
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_base_model"


@pytest.mark.anyio
async def test_custom_endpoint_base_model_suggestion_uses_default_or_explicit_protocol(api_client, model_catalog):
    provider_response = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers",
        json={
            "type": "openai",
            "name": "OpenAI relay",
            "configuration": {"base_url": "https://relay.example/v1"},
            "credential": "secret",
        },
    )
    assert provider_response.status_code == 201
    provider_id = provider_response.json()["id"]
    suggestion_url = f"/api/v1/workspaces/{WORKSPACE_ID}/model-catalog/suggestions"
    model_catalog.results[("openai", "anthropic:claude-sonnet-4-6")] = ModelDeclarations(context_window_tokens=200_000)

    defaulted = await api_client.post(
        suggestion_url,
        json={"provider_id": provider_id, "upstream_model": "gpt-5"},
    )
    assert defaulted.status_code == 200
    assert defaulted.json()["source"] == "exact"
    assert [item["base_model"] for item in defaulted.json()["items"]] == ["openai:gpt-5"]
    assert defaulted.json()["items"][0]["model_api"] == "openai.responses"

    selected = await api_client.post(
        suggestion_url,
        json={
            "provider_id": provider_id,
            "upstream_model": "gpt-5",
            "model_api": "openai.chat_completions",
        },
    )
    assert selected.status_code == 200
    assert selected.json()["source"] == "exact"
    assert [item["base_model"] for item in selected.json()["items"]] == ["openai-chat:gpt-5"]

    claude = await api_client.post(
        suggestion_url,
        json={
            "provider_id": provider_id,
            "upstream_model": "relay-claude-sonnet-4-5",
            "model_api": "openai.chat_completions",
        },
    )
    assert claude.status_code == 200
    assert claude.json()["source"] == "name_tokens"
    assert [item["base_model"] for item in claude.json()["items"]] == ["anthropic:claude-sonnet-4-5"]
    assert claude.json()["items"][0]["model_api"] == "openai.chat_completions"

    claude_without_api = await api_client.post(
        suggestion_url,
        json={"provider_id": provider_id, "upstream_model": "my-claude-sonnet-4-6"},
    )
    assert claude_without_api.status_code == 200
    assert claude_without_api.json()["source"] == "name_tokens"
    assert [item["base_model"] for item in claude_without_api.json()["items"]] == ["anthropic:claude-sonnet-4-6"]
    assert claude_without_api.json()["items"][0]["model_api"] is None
    assert claude_without_api.json()["items"][0]["model_api_label"] is None
    assert claude_without_api.json()["items"][0]["declarations"]["context_window_tokens"] == 200_000

    create_without_api = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models",
        json={
            "key": "claude-without-api",
            "provider_id": provider_id,
            "name": "Claude without API",
            "upstream_model": "my-claude-sonnet-4-6",
        },
    )
    assert create_without_api.status_code == 400
    assert create_without_api.json()["error"]["code"] == "model_api_required"
