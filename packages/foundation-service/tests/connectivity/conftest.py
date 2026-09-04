from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.agents.models import AgentRecord
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.domain import InputBatchingPolicy
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    DefaultRoute,
    ExternalRef,
    InboundEvent,
    ProviderCompleteDecision,
    ProviderEligibleEventRouting,
    ProviderEventDecision,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
    ProviderRequestError,
)
from a13n_service.connectivity.ingress.raw_objects import IngressRawObjectStore
from a13n_service.connectivity.ingress.routes import RouteService
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import (
    OrganizationRecord,
    RoleBindingRecord,
    ServiceAccountRecord,
    UserRecord,
    WorkspaceRecord,
)
from a13n_service.secrets import InternalSecretService, SecretProtector
from a13n_service.storage import transaction
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)
ORG_ID = "org_abcdef1234567890"
WORKSPACE_ID = "ws_abcdef1234567890"
USER_ID = "usr_abcdef1234567890"
SERVICE_ACCOUNT_ID = "sa_abcdef1234567890"
AGENT_ID = "agt_abcdef1234567890"


@pytest.fixture(scope="session")
def github_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


class FakeIngressAdapter:
    provider_key = "fake"
    config_versions = frozenset({"fake_http_v1"})
    allows_runtime_ambiguity = False
    max_request_bytes = 1024 * 1024
    dedup_horizon_seconds = 3600

    def validate_config(self, value: object, *, config_version: str) -> dict[str, object]:
        if config_version != "fake_http_v1" or not isinstance(value, dict) or set(value) != {"installation_id"}:
            raise ValueError("invalid config")
        installation_id = value["installation_id"]
        if not isinstance(installation_id, str) or not installation_id:
            raise ValueError("invalid installation")
        return {"installation_id": installation_id}

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> dict[str, object]:
        if config_version != "fake_http_v1" or set(value) != {"token"} or not value["token"]:
            raise ValueError("invalid credentials")
        return dict(value)

    def configuration_identity(self, value: dict[str, object], *, config_version: str) -> object:
        del config_version
        return value["installation_id"]

    def validate_route(
        self,
        *,
        match: object,
        provider_policy: object,
        ingress_config: dict[str, object],
        config_version: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        del ingress_config, config_version
        if not isinstance(match, dict) or set(match) != {"channel"} or not isinstance(match["channel"], str):
            raise ValueError("invalid match")
        if provider_policy != {}:
            raise ValueError("invalid policy")
        return dict(match), {}

    def prove_non_overlap(self, left: dict[str, object], right: dict[str, object]) -> bool | None:
        return left["channel"] != right["channel"]

    async def authenticate_and_normalize(
        self,
        request: ProviderRequest,
        *,
        ingress_id: str,
        ingress_config: dict[str, object],
        credentials: dict[str, object],
        received_at: datetime,
    ) -> ProviderRequestDecision:
        del ingress_id
        if request.headers.get("authorization") != f"Bearer {credentials['token']}":
            return ProviderCompleteDecision(
                response=ProviderHttpResponse(status_code=401, body=b"unauthorized"),
            )
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError as error:
            raise ProviderRequestError(
                ProviderHttpResponse(status_code=400, body=b"invalid payload"),
                reason_code="invalid_payload",
            ) from error
        if not isinstance(payload, dict):
            raise ProviderRequestError(
                ProviderHttpResponse(status_code=400, body=b"invalid payload"),
                reason_code="invalid_payload",
            )
        installation_id = payload.get("installation_id")
        if installation_id != ingress_config["installation_id"]:
            return ProviderCompleteDecision(
                response=ProviderHttpResponse(status_code=401, body=b"unauthorized"),
            )
        event_id = payload.get("event_id")
        channel = payload.get("channel")
        text = payload.get("text")
        if not all(isinstance(item, str) and item for item in (event_id, channel, text)):
            raise ProviderRequestError(
                ProviderHttpResponse(status_code=400, body=b"invalid payload"),
                reason_code="invalid_payload",
            )
        return ProviderEventDecision(
            event=InboundEvent(
                identity_kind="delivery",
                external_event_id=event_id,
                normalization_version="fake_v1",
                type="message.created",
                received_at=received_at,
                text=text,
                context={"installation_id": installation_id, "channel": channel},
                refs={"conversation": ExternalRef(kind="channel", id=channel)},
                data={},
                ordering_key=event_id,
                retain_raw=payload.get("retain_raw") is True,
            ),
        )

    def route_matches(self, event: InboundEvent, match: dict[str, object], *, config_version: str) -> bool:
        del config_version
        return event.context.get("channel") == match.get("channel")

    def default_route(
        self,
        event: InboundEvent,
        ingress_config: dict[str, object],
        *,
        config_version: str,
    ) -> DefaultRoute:
        del ingress_config, config_version
        return DefaultRoute(
            input_mapping={
                "op": "object",
                "fields": {
                    "schema_version": {"op": "static", "value": "2"},
                    "content": {"op": "static", "value": []},
                    "structured_content": {"op": "select", "path": ["events"]},
                },
            },
            input_batching=InputBatchingPolicy(min_interval_ms=100, max_batch_events=10),
            provider_policy={},
        )

    def classify(
        self,
        event: InboundEvent,
        provider_policy: dict[str, object],
        ingress_config: dict[str, object],
        *,
        config_version: str,
    ) -> ProviderEligibleEventRouting:
        del provider_policy, ingress_config, config_version
        return ProviderEligibleEventRouting(
            kind="eligible",
            external_ref_key="conversation",
            provider_context={"channel": event.context["channel"]},
        )

    def acknowledge(self, receipt: AdmissionReceipt) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            status_code=202 if receipt.status == "pending" else 200,
            headers={"content-type": "application/json"},
            body=json.dumps(receipt.model_dump(mode="json"), sort_keys=True).encode(),
        )

    def failure_response(self, reason_code: str) -> ProviderHttpResponse:
        return ProviderHttpResponse(status_code=503, body=reason_code.encode())


def actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_connectivity_test",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-connectivity-test",
    )


def adapter_registry() -> AdapterRegistry[IngressAdapter]:
    return AdapterRegistry(
        (
            AdapterDefinition[IngressAdapter](
                key="fake",
                config_versions=frozenset({"fake_http_v1"}),
                factory=FakeIngressAdapter,
            ),
        )
    )


@pytest.fixture
def ingress_adapter_registry() -> AdapterRegistry[IngressAdapter]:
    return adapter_registry()


@pytest.fixture
async def connectivity_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "connectivity.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    await _seed_connectivity_database(sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
async def postgres_connectivity_sessions(pg_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(PostgreSQLConfig(url=pg_url))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    await _seed_connectivity_database(sessions)
    try:
        yield sessions
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(service_metadata().drop_all)
        await engine.dispose()


async def _seed_connectivity_database(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            UserRecord(
                id=USER_ID,
                email="connectivity@example.com",
                normalized_email="connectivity@example.com",
                name="Connectivity Admin",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ServiceAccountRecord(
                id=SERVICE_ACCOUNT_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                name="Ingress Runner",
                normalized_name="ingress runner",
                description=None,
                status="active",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            AgentRecord(
                id=AGENT_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                source="custom",
                name="Support",
                normalized_name="support",
                description=None,
                version=1,
                current_revision_id="agtr_connectivity_test",
                enabled=True,
                archived_at=None,
                duplicated_from_agent_id=None,
                duplicated_from_revision_id=None,
                created_by_type="user",
                created_by_id=USER_ID,
                updated_by_type="user",
                updated_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_connectivity_org",
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
                    id="rb_connectivity_admin",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="admin",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_connectivity_runner",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="service_account",
                    principal_id=SERVICE_ACCOUNT_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="runner",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )


@pytest.fixture
def connectivity_secrets(connectivity_sessions: async_sessionmaker[AsyncSession]) -> InternalSecretService:
    return InternalSecretService(
        connectivity_sessions,
        SecretProtector(key=b"k" * 32, encryption_key_id="connectivity-test"),
        clock=lambda: NOW,
    )


@pytest.fixture
def ingress_service(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets: InternalSecretService,
) -> IngressService:
    return IngressService(
        connectivity_sessions,
        adapter_registry(),
        connectivity_secrets,
        clock=lambda: NOW,
    )


@pytest.fixture
async def connectivity_objects(tmp_path: Path) -> LocalObjectStore:
    return await LocalObjectStore.create(tmp_path / "connectivity-objects")


@pytest.fixture
def ingress_event_service(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets: InternalSecretService,
    connectivity_objects: LocalObjectStore,
) -> IngressEventService:
    return IngressEventService(
        connectivity_sessions,
        adapter_registry(),
        connectivity_secrets,
        IngressRawObjectStore(connectivity_objects),
        request_max_bytes=1024 * 1024,
        raw_retention_seconds=3600,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1024 * 1024,
        ingress_pending_max_count=100,
        ingress_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=3600,
        clock=lambda: NOW,
    )


@pytest.fixture
def route_service(connectivity_sessions: async_sessionmaker[AsyncSession]) -> RouteService:
    return RouteService(
        connectivity_sessions,
        adapter_registry(),
        batch_max_events=100,
        batch_max_wait_seconds=300,
        clock=lambda: NOW,
    )
