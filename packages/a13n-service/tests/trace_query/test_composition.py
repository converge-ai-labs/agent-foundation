from __future__ import annotations

import base64

import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.settings import Settings
from a13n_service.storage import transaction
from a13n_service.trace_query import TraceQueryProviderRegistry
from a13n_service.trace_query.authorization import RunTraceAccessAuthorizer
from sqlalchemy import delete
from tests.hooks.support import hook_actor

from .test_service import Provider, summary

pytestmark = pytest.mark.anyio


@pytest.fixture
def trace_settings(tmp_path, service_sqlite_database):
    return Settings(
        service={"role": "control"},
        database={"backend": "sqlite", "sqlite_path": service_sqlite_database},
        redis={"backend": "memory"},
        objects={"backend": "local", "local_root": tmp_path / "objects"},
        filesystem={"root": tmp_path / "files"},
        secrets={
            "master_key_base64": base64.b64encode(b"0123456789abcdef0123456789abcdef").decode(),
            "encryption_key_id": "trace-test",
        },
        observability={"tracing": False, "query": {"provider": "fixture"}},
    )


async def authenticate(_request):
    return hook_actor()


async def test_default_app_queries_real_runs_and_conceals_unreadable_traces(
    trace_settings, trace_correlation, interaction_sessions
):
    owned = summary(correlation=trace_correlation)
    foreign = summary(
        id="trace-foreign",
        correlation=trace_correlation.model_copy(update={"workspace_id": "ws_other1234567890"}),
    )
    provider = Provider(items=(owned, foreign))
    registry = TraceQueryProviderRegistry()
    registry.register("fixture", lambda: provider)
    app = create_app(
        trace_settings,
        components=Components(request_authenticator=authenticate, trace_query_provider_registry=registry),
    )
    path = f"/api/v1/workspaces/{trace_correlation.workspace_id}/traces"
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(path)
            assert response.status_code == 200, response.text
            assert [item["id"] for item in response.json()["items"]] == ["trace-1"]
            item = response.json()["items"][0]
            assert item["correlation"]["run_id"] == trace_correlation.run_id
            assert "run_attempt_number" not in item
            assert "run_attempt_outcome" not in item
            assert provider.queries[0].organization_id == trace_correlation.organization_id
            assert provider.queries[0].workspace_id == trace_correlation.workspace_id
            for view in ("full", "compact"):
                detail = await client.get(f"{path}/trace-1", params={"view": view})
                assert detail.status_code == 200, detail.text
                assert detail.json()["correlation"]["run_attempt_id"] == trace_correlation.run_attempt_id

            provider.detail = foreign
            assert (await client.get(f"{path}/trace-foreign")).status_code == 404
            provider.detail = None
            assert (await client.get(f"{path}/trace-missing")).status_code == 404
            provider.detail = owned

            # A valid direct grant allows the collection, but not this Run.
            async with transaction(interaction_sessions) as database:
                binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
                binding.resource_type = "agent"
                binding.resource_id = "agt_other1234567890"
                binding.role_key = "viewer"
            hidden = await client.get(path)
            assert hidden.status_code == 200, hidden.text
            assert hidden.json()["items"] == []
            assert (await client.get(f"{path}/trace-1")).status_code == 404

            # No eligible grants: reject before making another backend request.
            async with transaction(interaction_sessions) as database:
                await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.id == "rb_hookws717171717"))
            query_count = len(provider.queries)
            assert (await client.get(path)).status_code == 404
            assert len(provider.queries) == query_count


@pytest.mark.parametrize("provider_key", ["fixture", "none"])
async def test_app_preserves_authorizer_override_and_disabled_provider(
    trace_settings, trace_correlation, interaction_sessions, provider_key
):
    class DenyingAuthorizer(RunTraceAccessAuthorizer):
        async def authorize_run_attempts(self, **kwargs):
            return {}

    provider = Provider(items=(summary(correlation=trace_correlation),))
    registry = TraceQueryProviderRegistry()
    registry.register("fixture", lambda: provider)
    payload = trace_settings.model_dump(mode="python")
    payload["observability"]["query"]["provider"] = provider_key
    trace_settings = Settings.model_validate(payload)
    app = create_app(
        trace_settings,
        components=Components(
            request_authenticator=authenticate,
            trace_query_provider_registry=registry,
            trace_access_authorizer=DenyingAuthorizer(interaction_sessions),
        ),
    )
    path = f"/api/v1/workspaces/{trace_correlation.workspace_id}/traces"
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(path)
            if provider_key == "none":
                assert response.status_code == 503, response.text
                assert (await client.get(f"{path}/trace-1")).status_code == 503
                assert provider.queries == []
            else:
                assert response.status_code == 200, response.text
                assert response.json()["items"] == []
                assert (await client.get(f"{path}/trace-1")).status_code == 404
