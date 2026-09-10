from __future__ import annotations

import base64
import os
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import anyio
import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.observability import RunAttemptCorrelation, TraceContent, build_observability_runtime
from a13n_service.settings import ProcessRole, Settings
from a13n_service.storage import transaction
from a13n_service.trace_query import (
    AuthorizedRunAttempt,
    LangfuseTraceQueryProvider,
    ProviderTraceQuery,
    SearchIn,
    TraceCorrelation,
    TraceQueryScope,
    TraceView,
)
from fastapi import Request

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
    service_sqlite_database: Path,
) -> None:
    assert _BASE_URL is not None
    assert _PUBLIC_KEY is not None
    assert _SECRET_KEY is not None

    suffix = uuid4().hex[:12]
    organization_id = f"org_{suffix}"
    workspace_id = f"ws_{suffix}"
    run_attempt_id = f"attempt-it-{suffix}"
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
        organization_id=organization_id,
        workspace_id=workspace_id,
        session_id=f"session-it-{suffix}",
        thread_id=f"thread-it-{suffix}",
        run_id=f"run-it-{suffix}",
        run_attempt_id=run_attempt_id,
        run_attempt_number=1,
        agent_id=f"preset-it-{suffix}",
        agent_revision_id=f"preset-revision-it-{suffix}",
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
        assert summary.input == {"prompt": search_token}
        assert summary.output == {"answer": "integration-ok"}

        output_page = await provider.list_traces(replace(query, query="integration-ok", search_in=SearchIn.output))
        assert [item.id for item in output_page.items] == [summary.id]

        detail = await provider.get_trace(summary.id, TraceView.full)

    assert detail is not None
    assert detail.trace.correlation.workspace_id == workspace_id
    observations = {item.name: item for item in detail.observations}
    assert observations.keys() == {"a13n.service.run_attempt", "a13n.service.reconstruct"}
    assert observations["a13n.service.run_attempt"].parent_id is None
    assert observations["a13n.service.reconstruct"].parent_id == observations["a13n.service.run_attempt"].id

    actor = AuthenticatedActor(
        principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=f"user_{uuid4().hex[:16]}"),
        auth_method="integration-test",
        credential_id=f"credential-it-{suffix}",
        boundary_workspace_id=workspace_id,
    )

    async def authenticate(_request: Request) -> AuthenticatedActor:
        return actor

    class Authorizer:
        async def resolve_scope(
            self,
            *,
            actor: AuthenticatedActor,
            workspace_id: str,
        ) -> TraceQueryScope:
            assert actor is not None
            assert workspace_id == correlation.workspace_id
            return TraceQueryScope(organization_id, workspace_id)

        async def authorize_run_attempts(
            self,
            *,
            actor: AuthenticatedActor,
            scope: TraceQueryScope,
            correlations: Sequence[TraceCorrelation],
        ) -> dict[str, AuthorizedRunAttempt]:
            assert actor is not None
            assert scope == TraceQueryScope(organization_id, workspace_id)
            return {
                item.run_attempt_id: AuthorizedRunAttempt(item.run_attempt_id, 1, "succeeded")
                for item in correlations
                if item.run_attempt_id == run_attempt_id
            }

    settings = Settings(
        service={"role": ProcessRole.control},
        database={"backend": "sqlite", "sqlite_path": service_sqlite_database},
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
            trace_access_authorizer=Authorizer(),
        ),
    )
    async with app.router.lifespan_context(app):
        # The ordinary HTTP boundary resolves workspace references before the
        # test authorizer runs. Keep schema and identity scope fixture-owned.
        async with transaction(app.state.runtime.shared.storage.sessions) as session:
            session.add(
                OrganizationRecord(
                    id=organization_id, key="trace-test", name="Trace test", created_at=now, updated_at=now
                )
            )
            await session.flush()
            session.add(
                WorkspaceRecord(
                    id=workspace_id,
                    organization_id=organization_id,
                    key="trace-test",
                    name="Trace test",
                    created_at=now,
                    updated_at=now,
                )
            )
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                f"/api/v1/workspaces/{workspace_id}/traces",
                params={"query": search_token, "search_in": "input", "run_attempt_id": run_attempt_id},
            )
            assert response.status_code == 200
            public_summary = response.json()["items"][0]
            assert public_summary["run_attempt_number"] == 1
            assert public_summary["run_attempt_outcome"] == "succeeded"

            detail_response = await client.get(f"/api/v1/workspaces/{workspace_id}/traces/{public_summary['id']}")

    assert detail_response.status_code == 200
    assert detail_response.json()["trace"]["run_attempt_id"] == run_attempt_id
