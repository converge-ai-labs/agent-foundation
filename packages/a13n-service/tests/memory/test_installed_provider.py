import json
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.memory.models import MemoryProviderRecord
from a13n_service.storage import short_session
from anyio import run_process
from sqlalchemy import select

from ..models.conftest import WORKSPACE_ID, actor
from ..models.test_router import settings

pytestmark = pytest.mark.anyio


async def test_installed_memory_structured_secrets_removal_and_current_eligibility(
    memory_sessions, service_database, tmp_path, monkeypatch
):
    target = tmp_path / "installed"
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
    calls = []

    async def remote(self, request):
        assert str(request.url) == "https://memory.acme.example/facts/search"
        body = json.loads(request.content)
        calls.append((request.headers.get("x-api-key"), request.headers.get("x-acme-revision"), body))
        return httpx2.Response(200, json={"results": [{"id": "record", "memory": "fact", **body["filters"]}]})

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", remote)

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
        definitions = (await client.get("/api/v1/memory-provider-types")).json()["items"]
        definition = next(item for item in definitions if item["type"] == "acme_memory")
        assert definition["setup_label"] == "Configure Acme Memory"
        assert definition["supports_documents"]
        assert definition["authentication"]["mode"] == "required"
        base = f"/api/v1/workspaces/{WORKSPACE_ID}/memory-providers"
        created = await client.post(
            base,
            json={
                "type": "acme_memory",
                "name": "Installed",
                "credential": {"authorization": {"token": "first-secret"}, "revision": 7},
            },
        )
        assert created.status_code == 201, created.text
        assert "first-secret" not in created.text
        provider_id = created.json()["id"]
        path = base + "/" + provider_id
        etag = created.headers["etag"]

        async def search():
            return await client.post(path + "/memories/search", params={"scope": "user"}, json={"query": "fact"})

        assert (await search()).status_code == 200
        assert calls[-1][:2] == ("first-secret", "7")
        async with short_session(memory_sessions) as session:
            record = await session.get(MemoryProviderRecord, provider_id)
            assert b"first-secret" not in record.ciphertext
            assert json.loads(record.credential_snapshot().decrypt(config.secret_protector())) == {
                "authorization": {"token": "first-secret"},
                "revision": 7,
            }
        retained = await client.patch(path, headers={"If-Match": etag}, json={"name": "Renamed"})
        assert retained.status_code == 200
        etag = retained.headers["etag"]
        assert (await search()).status_code == 200
        assert calls[-1][:2] == ("first-secret", "7")
        replaced = await client.patch(
            path,
            headers={"If-Match": etag},
            json={"credential": {"authorization": {"token": "second-secret"}, "revision": 8}},
        )
        assert replaced.status_code == 200
        etag = replaced.headers["etag"]
        assert (await search()).status_code == 200
        assert calls[-1][:2] == ("second-secret", "8")
        removed = await client.patch(path, headers={"If-Match": etag}, json={"credential": None})
        assert removed.status_code == 200 and not removed.json()["credential_configured"], removed.text
        assert removed.headers["etag"] != etag
        repeated = await client.patch(path, headers={"If-Match": removed.headers["etag"]}, json={"credential": None})
        assert repeated.status_code == 200 and repeated.headers["etag"] == removed.headers["etag"]
        stale = await client.patch(path, headers={"If-Match": etag}, json={"name": "Stale"})
        assert stale.status_code == 412
        before = len(calls)
        assert (await search()).status_code == 409
        assert len(calls) == before
        async with short_session(memory_sessions) as session:
            record = await session.get(MemoryProviderRecord, provider_id)
            assert record.ciphertext is record.nonce is record.encryption_key_id is None
            assert record.credential_generation == 3
            audits = (
                await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.resource_id == provider_id))
            ).all()
            assert any(
                item.action == "memory_provider.update" and item.details["changed_fields"] == ["credential"]
                for item in audits
            )
        restored = await client.patch(
            path,
            headers={"If-Match": removed.headers["etag"]},
            json={"credential": {"authorization": {"token": "restored"}, "revision": 9}},
        )
        assert restored.status_code == 200
        assert (await search()).status_code == 200
        disabled = await client.patch(path, headers={"If-Match": restored.headers["etag"]}, json={"enabled": False})
        assert disabled.status_code == 200
        before = len(calls)
        assert (await search()).status_code == 409
        assert len(calls) == before
        for access in ("optional", "public"):
            created = await client.post(
                base, json={"type": "acme_memory", "name": access, "configuration": {"access": access}}
            )
            assert created.status_code == 201 and not created.json()["credential_configured"], created.text
            result = await client.post(
                base + "/" + created.json()["id"] + "/memories/search", params={"scope": "user"}, json={"query": "fact"}
            )
            assert result.status_code == 200, result.text
            assert calls[-1][:2] == (None, None)
