import httpx2
import pytest
from a13n_service.app import Components, create_app

from .conftest import WORKSPACE_ID, actor
from .test_router import settings

pytestmark = pytest.mark.anyio


async def test_installed_model_uses_current_structured_credentials_through_http(
    model_sessions, service_database, tmp_path, monkeypatch
):
    import ipaddress
    import json
    from pathlib import Path

    from a13n_harness.providers import endpoint_policy
    from a13n_service.models.models import ModelProviderRecord
    from a13n_service.storage import short_session
    from anyio import run_process

    target = tmp_path / "installed-model"
    await run_process(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target),
            str(Path(__file__).resolve().parents[4] / "examples/provider-plugin"),
        ]
    )
    monkeypatch.syspath_prepend(str(target))
    resolve = endpoint_policy._resolve_addresses
    monkeypatch.setattr(
        endpoint_policy,
        "_resolve_addresses",
        lambda host, port: (
            (ipaddress.ip_address("93.184.216.34"),) if host == "models.acme.example" else resolve(host, port)
        ),
    )
    observed = []

    async def vendor(self, request):
        assert str(request.url) == "https://models.acme.example/v1/chat/completions"
        observed.append(
            (request.headers["authorization"], request.headers["x-acme-index"], request.headers["x-acme-revision"])
        )
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            },
        )

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", vendor)

    async def authenticate(_request):
        return actor()

    config = settings(tmp_path, service_database)
    config = config.model_copy(
        update={"provider_plugins": config.provider_plugins.model_copy(update={"enabled": ("acme",)})}
    )
    app = create_app(config, components=Components(request_authenticator=authenticate))
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(base_url="http://testserver", transport=httpx2.ASGITransport(app=app)) as client,
    ):
        base = f"/api/v1/workspaces/{WORKSPACE_ID}"
        discovered = await client.get("/api/v1/model-provider-types")
        assert discovered.status_code == 200, discovered.text
        custom = next(item for item in discovered.json()["items"] if item["type"] == "acme_model")
        assert custom["setup_url"] == "https://docs.example.com/model-setup"
        assert custom["setup_label"] == "Configure Acme access"
        assert custom["supports_connection_probe"] is False
        created = await client.post(
            base + "/model-providers",
            json={
                "type": "acme_model",
                "name": "Installed model",
                "configuration": {"index": "guides"},
                "credential": {"authorization": {"token": "first-secret"}, "revision": 1},
            },
        )
        assert created.status_code == 201, created.text
        assert "first-secret" not in created.text
        provider_path = base + "/model-providers/" + created.json()["id"]
        saved = await client.post(
            base + "/models",
            json={
                "provider_id": created.json()["id"],
                "key": "installed/model",
                "name": "Fixture",
                "upstream_model": "fixture-model",
                "model_api": "openai.chat_completions",
            },
        )
        assert saved.status_code == 201, saved.text
        model_path = base + "/models/" + saved.json()["id"]
        tested = await client.post(model_path + "/test", json={})
        assert tested.status_code == 200 and tested.json()["success"], tested.text
        rotated = await client.patch(
            provider_path,
            headers={"If-Match": created.headers["ETag"]},
            json={
                "configuration": {"index": "reference"},
                "credential": {"authorization": {"token": "rotated-secret"}, "revision": 2},
            },
        )
        assert rotated.status_code == 200, rotated.text
        async with short_session(model_sessions) as session:
            record = await session.get(ModelProviderRecord, created.json()["id"])
            secrets = json.loads(record.credential_snapshot().decrypt(config.secret_protector()))
            assert secrets["credential"] == {"authorization": {"token": "rotated-secret"}, "revision": 2}
        tested = await client.post(model_path + "/test", json={})
        assert tested.status_code == 200 and tested.json()["success"], tested.text
        disabled = await client.patch(
            provider_path, headers={"If-Match": rotated.headers["ETag"]}, json={"enabled": False}
        )
        assert disabled.status_code == 200
        tested = await client.post(model_path + "/test", json={})
        assert tested.status_code == 409 or not tested.json()["success"]
        assert observed == [("Bearer first-secret", "guides", "1"), ("Bearer rotated-secret", "reference", "2")]


async def test_http_authentication_transitions_validate_resulting_patch_state(
    model_sessions, service_database, tmp_path
):
    async def authenticate(_request):
        return actor()

    app = create_app(settings(tmp_path, service_database), components=Components(request_authenticator=authenticate))
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(base_url="http://testserver", transport=httpx2.ASGITransport(app=app)) as client,
    ):
        base = f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers"
        missing = await client.post(base, json={"type": "openai", "name": "Missing"})
        assert missing.status_code == 400
        created = await client.post(
            base, json={"type": "openai", "name": "Public endpoint", "configuration": {"auth_mode": "none"}}
        )
        assert created.status_code == 201
        path = base + "/" + created.json()["id"]
        etag = created.headers["etag"]
        rejected = await client.patch(path, headers={"If-Match": etag}, json={"configuration": {"auth_mode": "bearer"}})
        assert rejected.status_code == 400
        replaced = await client.patch(
            path,
            headers={"If-Match": etag},
            json={"configuration": {"auth_mode": "bearer"}, "credential": {"api_key": "private-secret"}},
        )
        assert replaced.status_code == 200 and replaced.json()["credential_configured"]
        assert b"private-secret" not in replaced.content
        rejected = await client.patch(
            path, headers={"If-Match": replaced.headers["etag"]}, json={"configuration": {"auth_mode": "none"}}
        )
        assert rejected.status_code == 400
        removed = await client.patch(
            path,
            headers={"If-Match": replaced.headers["etag"]},
            json={"configuration": {"auth_mode": "none"}, "credential": None},
        )
        assert removed.status_code == 200 and not removed.json()["credential_configured"]
