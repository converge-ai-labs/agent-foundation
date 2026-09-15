from __future__ import annotations

import base64
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import anyio
import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam import AuthenticatedActor
from a13n_service.interactions.models import RunAttemptRecord
from a13n_service.observability import RunAttemptCorrelation, TraceContent, build_observability_runtime
from a13n_service.settings import ProcessRole, Settings
from a13n_service.storage import transaction
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.trace_query import (
    LangfuseTraceQueryProvider,
    ProviderTraceQuery,
    ProviderTraceRead,
    SearchIn,
    TraceCorrelation,
    TraceView,
)
from fastapi import Request
from tests.hooks.support import hook_actor
from tests.interactions.conftest import AGENT_REVISION_ID

_BASE_URL = os.getenv("A13N_TEST_LANGFUSE_BASE_URL")
_PUBLIC_KEY = os.getenv("A13N_TEST_LANGFUSE_PUBLIC_KEY")
_SECRET_KEY = os.getenv("A13N_TEST_LANGFUSE_SECRET_KEY")

pytestmark = pytest.mark.skipif(
    not all((_BASE_URL, _PUBLIC_KEY, _SECRET_KEY)),
    reason="A13N_TEST_LANGFUSE_* is not configured",
)


@pytest.mark.anyio
async def test_otlp_trace_round_trips_through_langfuse_v4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    service_database: PostgreSQLConfig,
    interaction_sessions,
    trace_correlation: TraceCorrelation,
) -> None:
    assert _BASE_URL is not None
    assert _PUBLIC_KEY is not None
    assert _SECRET_KEY is not None

    suffix = uuid4().hex[:12]
    organization_id = trace_correlation.organization_id
    workspace_id = trace_correlation.workspace_id
    run_attempt_id = trace_correlation.run_attempt_id
    search_token = f"trace-integration-{suffix}"
    authorization = base64.b64encode(f"{_PUBLIC_KEY}:{_SECRET_KEY}".encode()).decode()
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"{_BASE_URL.rstrip('/')}/api/public/otel")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_HEADERS",
        f"Authorization=Basic%20{authorization},x-langfuse-ingestion-version=4",
    )

    runtime = build_observability_runtime(
        enabled=True,
        trace_content=TraceContent.standard,
        service_name="a13n-service-integration-test",
        service_version="test",
        deployment_environment="integration",
        service_role="worker",
        shutdown_timeout_seconds=10,
    )
    correlation = RunAttemptCorrelation(
        **trace_correlation.model_dump(),
        run_attempt_number=1,
        agent_revision_id=AGENT_REVISION_ID,
    )
    with runtime.run_attempt(correlation, input_value={"prompt": search_token}) as attempt:
        with attempt.phase("a13n.service.reconstruct"):
            pass
        attempt.set_outcome("succeeded", output_value={"answer": "integration-ok"})
    await runtime.aclose()

    now = datetime.now(UTC)
    query = ProviderTraceQuery(
        organization_id=organization_id,
        workspace_id=workspace_id,
        from_started_at=now - timedelta(minutes=5),
        to_started_at=now + timedelta(minutes=5),
        limit=100,
        view=TraceView.full,
        query=search_token,
        search_in=SearchIn.input,
        run_attempt_id=run_attempt_id,
    )
    async with httpx2.AsyncClient(follow_redirects=False, timeout=5, trust_env=False) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url=_BASE_URL,
            public_key=_PUBLIC_KEY,
            secret_key=_SECRET_KEY,
        )
        page = None
        with anyio.fail_after(30):
            while page is None or not page.items:
                page = await provider.list_traces(query)
                if not page.items:
                    await anyio.sleep(0.5)

        assert len(page.items) == 1
        summary = page.items[0]
        assert summary.correlation.run_attempt_id == run_attempt_id
        assert summary.root.input.value == {"prompt": search_token}
        assert summary.root.output.value == {"answer": "integration-ok"}

        output_page = await provider.list_traces(replace(query, query="integration-ok", search_in=SearchIn.output))
        assert [item.id for item in output_page.items] == [summary.id]

        read = ProviderTraceRead(
            organization_id=organization_id,
            workspace_id=workspace_id,
            trace_id=summary.id,
            history_from=None,
            to_started_at=now + timedelta(minutes=5),
            view=TraceView.full,
        )
        detail = await provider.get_trace(read)
        observation_page = await provider.list_observations(read)

    assert detail is not None
    assert detail.correlation.workspace_id == workspace_id
    observations = {item.name: item for item in observation_page.items}
    assert observations.keys() == {"a13n.service.run_attempt", "a13n.service.reconstruct"}
    assert observations["a13n.service.run_attempt"].parent_id is None
    assert observations["a13n.service.reconstruct"].parent_id == observations["a13n.service.run_attempt"].id

    async def authenticate(_request: Request) -> AuthenticatedActor:
        return replace(
            hook_actor(), auth_method="session", boundary_workspace_id=None, boundary_organization_id=organization_id
        )

    async with transaction(interaction_sessions) as database:
        record = await database.get(RunAttemptRecord, run_attempt_id)
        assert record is not None
        record.status = "succeeded"
        record.finished_at = now

    settings = Settings(
        service={"role": ProcessRole.control},
        database={"url": service_database.url.get_secret_value()},
        redis={"backend": "memory"},
        objects={"backend": "local", "local_root": tmp_path / "objects"},
        filesystem={"root": tmp_path / "files"},
        secrets={
            "master_key_base64": base64.b64encode(b"0123456789abcdef0123456789abcdef").decode(),
            "encryption_key_id": "trace-integration-test",
        },
        observability={
            "tracing": False,
            "query": {
                "provider": "langfuse",
                "langfuse_base_url": _BASE_URL,
                "langfuse_public_key": _PUBLIC_KEY,
                "langfuse_secret_key": _SECRET_KEY,
            },
        },
    )
    app = create_app(
        settings,
        components=Components(
            request_authenticator=authenticate,
        ),
    )
    async with app.router.lifespan_context(app):
        # Only authentication is supplied by the Host. Default composition must
        # authorize the real persisted Run through current Service IAM grants.
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                f"/api/v1/workspaces/{workspace_id}/traces",
                params={"query": search_token, "search_in": "input", "run_attempt_id": run_attempt_id},
            )
            assert response.status_code == 200
            public_summary = response.json()["items"][0]
            assert public_summary["correlation"]["run_attempt_id"] == run_attempt_id
            assert "run_attempt_outcome" not in public_summary

            descriptor_response = await client.get(f"/api/v1/workspaces/{workspace_id}/trace-query")
            assert descriptor_response.status_code == 200
            assert descriptor_response.json()["search_in"] == ["input", "output"]
            assert descriptor_response.json()["enabled"] is True
            detail_response = await client.get(f"/api/v1/workspaces/{workspace_id}/traces/{public_summary['id']}")
            observations_response = await client.get(
                f"/api/v1/workspaces/{workspace_id}/traces/{public_summary['id']}/observations",
                params={"view": "full", "limit": 1},
            )
            assert observations_response.status_code == 200
            observations_page = observations_response.json()
            assert len(observations_page["items"]) == 1
            assert observations_page["items"][0]["status"] is None
            assert observations_page["next_cursor"] is not None
            second_response = await client.get(
                f"/api/v1/workspaces/{workspace_id}/traces/{public_summary['id']}/observations",
                params={"view": "full", "limit": 1, "cursor": observations_page["next_cursor"]},
            )
            assert second_response.status_code == 200
            second_page = second_response.json()
            assert second_page["next_cursor"] is None
            public_ids = {item["id"] for item in observations_page["items"] + second_page["items"]}
            assert public_ids == {item.id for item in observation_page.items}
            assert detail_response.json()["root"]["id"] in public_ids

    assert detail_response.status_code == 200
    assert detail_response.json()["correlation"]["run_attempt_id"] == run_attempt_id
