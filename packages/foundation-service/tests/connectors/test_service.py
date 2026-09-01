from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_service.connectors import (
    ConnectionService,
    ConnectionSetupService,
    ConnectionStatus,
    ConnectorError,
    ConnectorEventTriggerSource,
    ConnectorProvider,
    ConnectorProviderAccount,
    ConnectorProviderCapabilities,
    ConnectorProviderCatalog,
    ConnectorProviderConnectionResult,
    ConnectorProviderContext,
    ConnectorProviderEvent,
    ConnectorProviderEventSourceResult,
    ConnectorProviderMetadata,
    ConnectorProviderRegistration,
    ConnectorProviderRuntime,
    ConnectorProviderSecret,
    ConnectorProviderSetupResult,
    ConnectorProviderTool,
    ConnectorProviderToolResult,
    ConnectorReauthorizationRequired,
    ConnectorService,
    CreateConnector,
    CreateConnectorRevision,
    CreateTrigger,
    PrincipalRef,
    ScheduleTriggerSource,
    StartConnectionSetup,
    TriggerIngressService,
    TriggerService,
    TriggerStatus,
    UpdateConnector,
    resolve_connection,
)
from a13n_service.connectors.models import ConnectionRecord, ConnectionSetupRecord, TriggerRecord
from a13n_service.database import DatabaseMigrator
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ORG_ID = "org_0000000000000001"
OTHER_ORG_ID = "org_0000000000000002"
WORKSPACE_ID = "ws_0000000000000001"
USER_ID = "usr_0000000000000001"
OTHER_USER_ID = "usr_0000000000000002"


class _Provider(ConnectorProvider):
    require_reauthorization = False
    fail_revoke = False
    fail_stop = False
    last_call_had_secret = False
    last_complete_operation_id: str | None = None
    last_reconcile_operation_id: str | None = None
    last_reconcile_had_source: bool | None = None

    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name="Test",
            description="Test Provider",
            contract_version="1",
            provider_config_schemas={"1": {"type": "object"}},
            capabilities=ConnectorProviderCapabilities(
                tools=True,
                connections=True,
                events=True,
                event_delivery="webhook",
            ),
            connection_setup_modes=("manual",),
        )

    def validate_config(self, provider_config_version: str, config) -> None:
        if provider_config_version != "1" or config.get("invalid"):
            raise ValueError("invalid config")

    async def list_tools(self, context, **kwargs):
        return (
            ConnectorProviderTool(
                name="create_issue",
                tool_id="github.create_issue",
                description="Create one issue",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
                effects=("write", "external_communication"),
                credential_audiences=("github_api",),
                idempotency="provider_key",
                output_policy={
                    "max_inline_bytes": 1024,
                    "max_output_bytes": 4096,
                    "overflow": "spill",
                    "redact": True,
                },
            ),
        )

    async def call_tool(self, context, **kwargs):
        self.last_call_had_secret = bool(kwargs["connection"].secrets)
        return ConnectorProviderToolResult(value={"created": kwargs["arguments"]["title"]})

    def connection_spec(self, provider_config_version, config):
        return {"type": "object"}

    async def start_connection(self, context, **kwargs):
        if kwargs["input"].get("pending"):
            return ConnectorProviderSetupResult(
                completed=False,
                next_action={"type": "redirect", "url": "https://provider.example.test/authorize"},
                continuation_state={"verifier": "private-verifier"},
            )
        return ConnectorProviderSetupResult(completed=True, connection=_connection_result("setup-token"))

    async def complete_connection(self, context, **kwargs):
        self.last_complete_operation_id = context.operation_id
        assert kwargs["continuation_state"]["verifier"] == "private-verifier"
        return _connection_result("callback-token")

    async def refresh_connection(self, context, **kwargs):
        if self.require_reauthorization:
            raise ConnectorReauthorizationRequired()
        return ConnectorProviderConnectionResult(
            provider_state_version="2",
            provider_state={"installation": "refreshed"},
            account=ConnectorProviderAccount(external_id="account-1", display_name="Refreshed Account"),
            secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("refreshed-token")),),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def revoke_connection(self, context, **kwargs):
        if self.fail_revoke:
            raise RuntimeError("unknown external outcome")

    def validate_connection(self, **kwargs):
        return None

    async def list_events(self, context, **kwargs):
        return ()

    def validate_event_config(self, **kwargs):
        return None

    async def start_event_source(self, context, **kwargs):
        return ConnectorProviderEventSourceResult(
            provider_state_version="1",
            provider_state={"subscription": "sub-1"},
            secrets=(ConnectorProviderSecret(key="signature", value=SecretStr("webhook-secret")),),
        )

    async def stop_event_source(self, context, **kwargs):
        if self.fail_stop:
            raise RuntimeError("unknown stop outcome")
        return None

    async def renew_event_source(self, context, **kwargs):
        return kwargs["source"]

    async def reconcile_event_source(self, context, **kwargs):
        self.last_reconcile_operation_id = context.operation_id
        self.last_reconcile_had_source = kwargs["source"] is not None
        return kwargs["source"] or ConnectorProviderEventSourceResult(
            provider_state_version="1",
            provider_state={"subscription": "sub-reconciled"},
            secrets=(ConnectorProviderSecret(key="signature", value=SecretStr("reconciled-secret")),),
        )

    async def receive_webhook(self, context, **kwargs):
        return (ConnectorProviderEvent(event_id="event-1", event_type="push", data={}),)


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[str, tuple[ConnectorProviderSecret, ...]] = {}

    async def replace_connection_secrets(self, session, *, connection_id, secrets, **kwargs):
        self.values[connection_id] = tuple(secrets)

    async def read_connection_secrets(self, *, connection_id, **kwargs):
        return self.values[connection_id]

    async def delete_connection_secrets(self, session, *, connection_id, **kwargs):
        self.values.pop(connection_id, None)


class _TriggerSecrets:
    def __init__(self) -> None:
        self.values: dict[str, tuple[ConnectorProviderSecret, ...]] = {}

    async def replace_trigger_secrets(self, session, *, trigger_id, secrets, **kwargs):
        self.values[trigger_id] = tuple(secrets)

    async def read_trigger_secrets(self, *, trigger_id, **kwargs):
        return self.values[trigger_id]

    async def delete_trigger_secrets(self, session, *, trigger_id, **kwargs):
        self.values.pop(trigger_id, None)


class _SetupProtector:
    def __init__(self) -> None:
        self.states: dict[str, tuple[str, datetime]] = {}

    def encrypt_setup_state(self, setup_id, value):
        del setup_id
        return bytes(byte ^ 0xA5 for byte in json.dumps(value, sort_keys=True).encode())

    def decrypt_setup_state(self, setup_id, ciphertext):
        del setup_id
        return json.loads(bytes(byte ^ 0xA5 for byte in ciphertext).decode())

    def issue_callback_state(self, setup_id, provider_key, expires_at):
        state = f"state_{setup_id}"
        self.states[state] = (provider_key, expires_at)
        return state

    def resolve_callback_state(self, state, provider_key):
        stored_provider, expires_at = self.states[state]
        if stored_provider != provider_key or expires_at <= datetime.now(UTC):
            raise ValueError("invalid state")
        return state.removeprefix("state_")

    def digest_setup_request(self, value):
        return hashlib.sha256(b"test-key" + value).hexdigest()


class _AgentTargets:
    def __init__(self) -> None:
        self.validations = 0

    async def validate_trigger_target(self, **kwargs):
        self.validations += 1


class _ToolAuthorizer:
    def __init__(self) -> None:
        self.calls = 0

    async def authorize_connector_tool(self, **kwargs):
        self.calls += 1

    async def authorize_connector_discovery(self, **kwargs):
        self.calls += 1


class _TurnAcceptor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def prepare_trigger_turn(self, **kwargs):
        self.calls.append(kwargs)
        return {"turn_id": f"turn_000000000000000{len(self.calls)}"}

    async def commit_trigger_turn(self, session, *, prepared, source):
        del session, source
        return prepared["turn_id"]


@pytest.fixture
def providers() -> ConnectorProviderCatalog:
    provider = _Provider()
    registration = ConnectorProviderRegistration(
        provider_key="test",
        class_module=__name__,
        class_qualname="_Provider",
        import_target="tests:_Provider",
        distribution_name="a13n-connector-test",
        distribution_version="1.0.0",
        metadata=provider.metadata,
    )
    return ConnectorProviderCatalog(((registration, provider),))


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    config = SQLiteConfig(path=tmp_path / "foundation.sqlite3")
    DatabaseMigrator(config).upgrade()
    engine = create_sql_engine(config)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    async with transaction(factory) as session:
        session.add(
            OrganizationRecord(
                id=ORG_ID,
                name="Test Organization",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Test Workspace",
                normalized_name="test-workspace",
                version=1,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()


def _actor(identifier: str = USER_ID) -> PrincipalRef:
    return PrincipalRef(principal_type="user", principal_id=identifier)


def _connection_result(token: str) -> ConnectorProviderConnectionResult:
    return ConnectorProviderConnectionResult(
        provider_state_version="1",
        provider_state={"installation": "setup"},
        account=ConnectorProviderAccount(external_id="account-setup", display_name="Setup Account"),
        secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr(token)),),
    )


def _create_request(**updates: object) -> CreateConnector:
    values: dict[str, object] = {
        "organization_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "name": "GitHub",
        "provider_key": "test",
        "provider_config_version": "1",
        "config": {"base": "https://example.test"},
        "created_by": _actor(),
    }
    values.update(updates)
    return CreateConnector.model_validate(values)


@pytest.mark.anyio
async def test_create_connector_atomically_creates_revision_one_and_tenant_reads(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    service = ConnectorService(sessions, providers)

    created = await service.create(_create_request())

    assert created.connector.version == 1
    assert created.connector.enabled is True
    assert created.revision.connector_id == created.connector.id
    assert created.revision.version == 1
    assert created.revision.config == {"base": "https://example.test"}
    assert await service.get(ORG_ID, WORKSPACE_ID, created.connector.id) == created.connector
    with pytest.raises(ConnectorError) as concealed:
        await service.get(OTHER_ORG_ID, WORKSPACE_ID, created.connector.id)
    assert concealed.value.code == "not_found"


@pytest.mark.anyio
async def test_connector_update_uses_cas_and_semantic_noop_does_not_advance(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    service = ConnectorService(sessions, providers)
    created = await service.create(_create_request())

    unchanged = await service.update(
        ORG_ID,
        WORKSPACE_ID,
        created.connector.id,
        UpdateConnector(name="GitHub", expected_version=1),
    )
    changed = await service.update(
        ORG_ID,
        WORKSPACE_ID,
        created.connector.id,
        UpdateConnector(enabled=False, expected_version=1),
    )
    cleared = await service.update(
        ORG_ID,
        WORKSPACE_ID,
        created.connector.id,
        UpdateConnector(description=None, expected_version=2),
    )

    assert unchanged.version == 1
    assert changed.version == 2
    assert changed.enabled is False
    assert cleared.version == 2
    assert cleared.description is None
    with pytest.raises(ConnectorError) as conflict:
        await service.update(
            ORG_ID,
            WORKSPACE_ID,
            created.connector.id,
            UpdateConnector(enabled=True, expected_version=1),
        )
    assert conflict.value.code == "version_conflict"
    assert conflict.value.details["current_version"] == 2


@pytest.mark.anyio
async def test_revision_creation_is_immutable_monotonic_and_deduplicates_noop(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    service = ConnectorService(sessions, providers)
    created = await service.create(_create_request())

    duplicate = await service.create_revision(
        ORG_ID,
        WORKSPACE_ID,
        created.connector.id,
        CreateConnectorRevision(
            provider_key="test",
            provider_config_version="1",
            config={"base": "https://example.test"},
            created_by=_actor(),
        ),
    )
    second = await service.create_revision(
        ORG_ID,
        WORKSPACE_ID,
        created.connector.id,
        CreateConnectorRevision(
            provider_key="test",
            provider_config_version="1",
            config={"base": "https://api.example.test"},
            created_by=_actor(),
        ),
    )

    assert duplicate.created is False
    assert duplicate.revision.id == created.revision.id
    assert second.created is True
    assert second.revision.version == 2
    assert [
        revision.version for revision in await service.list_revisions(ORG_ID, WORKSPACE_ID, created.connector.id)
    ] == [
        2,
        1,
    ]


@pytest.mark.anyio
async def test_invalid_provider_configuration_creates_nothing(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    service = ConnectorService(sessions, providers)

    with pytest.raises(ConnectorError) as rejected:
        await service.create(_create_request(config={"invalid": True}))

    assert rejected.value.code == "provider_config_incompatible"
    assert await service.list(ORG_ID, WORKSPACE_ID) == ()


async def _add_connection(
    sessions: async_sessionmaker[AsyncSession],
    *,
    connector_id: str,
    principal: PrincipalRef | None,
    name: str,
) -> str:
    now = datetime.now(UTC)
    connection_id = new_object_id("conn")
    async with transaction(sessions) as session:
        session.add(
            ConnectionRecord(
                id=connection_id,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                connector_id=connector_id,
                principal_type=principal.principal_type if principal else None,
                principal_id=principal.principal_id if principal else None,
                name=name,
                provider_key="test",
                provider_state_version="1",
                provider_state={},
                account_external_id=name,
                account_display_name=name,
                status=ConnectionStatus.active.value,
                expires_at=None,
                version=1,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=now,
                updated_at=now,
            )
        )
    return connection_id


@pytest.mark.anyio
async def test_connection_resolution_prefers_exact_personal_then_shared(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    service = ConnectorService(sessions, providers)
    connector = (await service.create(_create_request())).connector
    shared_id = await _add_connection(sessions, connector_id=connector.id, principal=None, name="shared")

    shared = await resolve_connection(
        sessions,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_id=connector.id,
        provider_key="test",
        principal=_actor(),
        pinned_connection_id=None,
    )
    personal_id = await _add_connection(sessions, connector_id=connector.id, principal=_actor(), name="personal")
    personal = await resolve_connection(
        sessions,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_id=connector.id,
        provider_key="test",
        principal=_actor(),
        pinned_connection_id=None,
    )

    assert shared is not None and shared.id == shared_id
    assert personal is not None and personal.id == personal_id


@pytest.mark.anyio
async def test_connection_resolution_rejects_ambiguity_and_ineligible_pin(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    service = ConnectorService(sessions, providers)
    connector = (await service.create(_create_request())).connector
    await _add_connection(sessions, connector_id=connector.id, principal=_actor(), name="first")
    other_id = await _add_connection(sessions, connector_id=connector.id, principal=_actor(OTHER_USER_ID), name="other")
    await _add_connection(sessions, connector_id=connector.id, principal=_actor(), name="second")

    with pytest.raises(ConnectorError) as ambiguous:
        await resolve_connection(
            sessions,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_id=connector.id,
            provider_key="test",
            principal=_actor(),
            pinned_connection_id=None,
        )
    assert ambiguous.value.code == "connection_ambiguous"

    with pytest.raises(ConnectorError) as denied:
        await resolve_connection(
            sessions,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_id=connector.id,
            provider_key="test",
            principal=_actor(),
            pinned_connection_id=other_id,
        )
    assert denied.value.code == "connection_required"


@pytest.mark.anyio
async def test_connection_setup_commits_connection_and_secrets_atomically(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector = await ConnectorService(sessions, providers).create(_create_request())
    secrets = _Secrets()
    setup = ConnectionSetupService(sessions, providers, secrets, _SetupProtector())
    request = StartConnectionSetup(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=connector.revision.id,
        principal_ref=_actor(),
        name="Personal GitHub",
        setup_mode="manual",
        input={"api_key": "write-only-value"},
        actor=_actor(),
    )
    context = ConnectorProviderContext(
        operation_id="op_setup",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )

    receipt = await setup.start(request, context=context)
    replay = await setup.start(request, context=context)

    assert receipt.completed is True
    assert receipt.connection is not None
    assert replay.connection is not None and replay.connection.id == receipt.connection.id
    assert receipt.connection.model_dump_json().find("write-only-value") == -1
    assert secrets.values[receipt.connection.id][0].value.get_secret_value() == "setup-token"
    async with sessions() as session:
        stored = await session.get(ConnectionSetupRecord, receipt.setup_id)
    assert stored is not None
    assert stored.continuation_ciphertext is None
    assert stored.status == "completed"

    changed_request = request.model_copy(update={"input": {"api_key": "different-value"}})
    with pytest.raises(ConnectorError) as conflict:
        await setup.start(changed_request, context=context)
    assert conflict.value.code == "version_conflict"


@pytest.mark.anyio
async def test_connection_callback_setup_is_single_completion_and_hides_continuation(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector = await ConnectorService(sessions, providers).create(_create_request())
    secrets = _Secrets()
    protector = _SetupProtector()
    setup = ConnectionSetupService(sessions, providers, secrets, protector)
    pending = await setup.start(
        StartConnectionSetup(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_revision_id=connector.revision.id,
            principal_ref=None,
            name="Shared GitHub",
            setup_mode="manual",
            input={"pending": True},
            actor=_actor(),
        ),
        context=ConnectorProviderContext(
            operation_id="op_setup_start",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        callback_url="https://foundation.example.test/api/v1/connector-callbacks/test",
    )
    state = next(iter(protector.states))
    async with sessions() as session:
        stored = await session.get(ConnectionSetupRecord, pending.setup_id)
    assert stored is not None
    assert stored.continuation_ciphertext is not None
    assert b"private-verifier" not in stored.continuation_ciphertext

    completed = await setup.complete_callback(
        provider_key="test",
        state=state,
        input={"code": "one-time-code"},
        context=ConnectorProviderContext(
            operation_id="op_setup_complete",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
    )
    replay = await setup.complete_callback(
        provider_key="test",
        state=state,
        input={"code": "one-time-code"},
        context=ConnectorProviderContext(
            operation_id="op_setup_replay",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
    )

    assert completed.connection is not None
    assert replay.connection is not None
    assert replay.connection.id == completed.connection.id
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(ConnectionRecord))
        stored = await session.get(ConnectionSetupRecord, pending.setup_id)
    assert count == 1
    assert stored is not None and stored.continuation_ciphertext is None


@pytest.mark.anyio
async def test_connection_callback_recovers_completing_with_stable_provider_operation(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector = await ConnectorService(sessions, providers).create(_create_request())
    setup = ConnectionSetupService(sessions, providers, _Secrets(), _SetupProtector())
    pending = await setup.start(
        StartConnectionSetup(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_revision_id=connector.revision.id,
            principal_ref=None,
            name="Shared GitHub",
            setup_mode="manual",
            input={"pending": True},
            actor=_actor(),
        ),
        context=ConnectorProviderContext(
            operation_id="op_setup_recover",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
    )
    async with transaction(sessions) as session:
        record = await session.get(ConnectionSetupRecord, pending.setup_id)
        assert record is not None
        record.status = "completing"

    completed = await setup.complete(
        setup_id=pending.setup_id,
        input={"code": "replayed-code"},
        context=ConnectorProviderContext(
            operation_id="different_http_request",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        actor=_actor(),
    )

    provider = providers.require("test")
    assert isinstance(provider, _Provider)
    assert provider.last_complete_operation_id == "op_setup_recover:complete"
    assert completed.completed is True
    assert completed.connection is not None


@pytest.mark.anyio
async def test_connection_provider_result_and_lifecycle_keep_secrets_private(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector_service = ConnectorService(sessions, providers)
    created = await connector_service.create(_create_request())
    secrets = _Secrets()
    service = ConnectionService(sessions, providers, secrets)
    result = ConnectorProviderConnectionResult(
        provider_state_version="1",
        provider_state={"installation": "one"},
        account=ConnectorProviderAccount(external_id="account-1", display_name="Account One"),
        secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("secret-token")),),
    )

    connection = await service.create_from_provider_result(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=created.revision.id,
        principal_ref=_actor(),
        name="Personal GitHub",
        result=result,
        actor=_actor(),
    )
    disabled = await service.disable(ORG_ID, WORKSPACE_ID, connection.id, expected_version=1)
    refreshed_disabled = await service.refresh(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=connection.id,
        connector_revision_id=created.revision.id,
        expected_version=2,
        context=ConnectorProviderContext(
            operation_id="op_refresh_disabled",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        actor=_actor(),
    )
    enabled = await service.enable(ORG_ID, WORKSPACE_ID, connection.id, expected_version=3)

    assert connection.status is ConnectionStatus.active
    assert connection.model_dump_json().find("secret-token") == -1
    assert secrets.values[connection.id][0].value.get_secret_value() == "refreshed-token"
    assert disabled.status is ConnectionStatus.disabled
    assert disabled.version == 2
    assert refreshed_disabled.status is ConnectionStatus.disabled
    assert refreshed_disabled.version == 3
    assert enabled.status is ConnectionStatus.active
    assert enabled.version == 4


@pytest.mark.anyio
async def test_connection_refresh_and_revoke_recheck_cas_and_revoke_locally_on_provider_failure(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector_service = ConnectorService(sessions, providers)
    created = await connector_service.create(_create_request())
    secrets = _Secrets()
    service = ConnectionService(sessions, providers, secrets)
    connection = await service.create_from_provider_result(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=created.revision.id,
        principal_ref=None,
        name="Shared GitHub",
        result=ConnectorProviderConnectionResult(
            provider_state_version="1",
            provider_state={},
            account=ConnectorProviderAccount(external_id="account-1", display_name="Account One"),
            secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("first-token")),),
        ),
        actor=_actor(),
    )
    context = ConnectorProviderContext(operation_id="op_refresh", deadline=datetime.now(UTC) + timedelta(seconds=30))

    refreshed = await service.refresh(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=connection.id,
        connector_revision_id=created.revision.id,
        expected_version=1,
        context=context,
        actor=_actor(),
    )
    provider = providers.require("test")
    assert isinstance(provider, _Provider)
    provider.fail_revoke = True
    revoked = await service.revoke(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=connection.id,
        connector_revision_id=created.revision.id,
        expected_version=2,
        context=ConnectorProviderContext(
            operation_id="op_revoke",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        actor=_actor(),
    )

    assert refreshed.account.display_name == "Refreshed Account"
    assert refreshed.version == 2
    assert secrets.values.get(connection.id) is not None
    assert revoked.status is ConnectionStatus.revoked
    assert revoked.version == 3

    provider.fail_revoke = False
    reconciled = await service.reconcile_revoke(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=connection.id,
        connector_revision_id=created.revision.id,
        context=ConnectorProviderContext(
            operation_id="op_reconcile",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
    )

    assert reconciled.status is ConnectionStatus.revoked
    assert reconciled.version == 4
    assert secrets.values.get(connection.id) is None


@pytest.mark.anyio
async def test_reauthorization_updates_same_connection_identity(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector = await ConnectorService(sessions, providers).create(_create_request())
    secrets = _Secrets()
    connections = ConnectionService(sessions, providers, secrets)
    connection = await connections.create_from_provider_result(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=connector.revision.id,
        principal_ref=_actor(),
        name="Personal GitHub",
        result=_connection_result("expired-token"),
        actor=_actor(),
    )
    provider = providers.require("test")
    assert isinstance(provider, _Provider)
    provider.require_reauthorization = True

    with pytest.raises(ConnectorReauthorizationRequired):
        await connections.refresh(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=connection.id,
            connector_revision_id=connector.revision.id,
            expected_version=1,
            context=ConnectorProviderContext(
                operation_id="op_refresh_invalid",
                deadline=datetime.now(UTC) + timedelta(seconds=30),
            ),
            actor=_actor(),
        )
    required = await connections.get(ORG_ID, WORKSPACE_ID, connection.id)
    assert required.status is ConnectionStatus.reauthorization_required
    provider.require_reauthorization = False

    receipt = await ConnectionSetupService(sessions, providers, secrets, _SetupProtector()).start(
        StartConnectionSetup(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_revision_id=connector.revision.id,
            connection_id=connection.id,
            principal_ref=_actor(),
            name="Personal GitHub",
            setup_mode="manual",
            input={"api_key": "replacement"},
            actor=_actor(),
        ),
        context=ConnectorProviderContext(
            operation_id="op_reauthorize",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
    )

    assert receipt.connection is not None
    assert receipt.connection.id == connection.id
    assert receipt.connection.status is ConnectionStatus.active
    assert receipt.connection.version == 3
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(ConnectionRecord))
    assert count == 1


@pytest.mark.anyio
async def test_schedule_trigger_is_created_disabled_and_uses_cas_lifecycle(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    targets = _AgentTargets()
    service = TriggerService(sessions, providers, _Secrets(), _TriggerSecrets(), targets)
    trigger = await service.create(
        CreateTrigger(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            name="Daily report",
            principal_ref=_actor(),
            agent_revision_id="agrev_1",
            source=ScheduleTriggerSource(type="interval", interval_seconds=60),
            input_template={"prompt": "generate report"},
            created_by=_actor(),
        )
    )
    context = ConnectorProviderContext(operation_id="op_schedule", deadline=datetime.now(UTC) + timedelta(seconds=30))
    active = await service.enable(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=1,
        context=context,
        callback_url="https://unused.example.test",
        actor=_actor(),
    )

    assert trigger.status is TriggerStatus.disabled
    assert active.status is TriggerStatus.active
    assert active.version == 2
    assert targets.validations == 1

    disabled = await service.disable(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=2,
        context=context,
        actor=_actor(),
    )
    assert disabled.status is TriggerStatus.disabled
    assert disabled.version == 3


@pytest.mark.anyio
async def test_connector_event_trigger_activates_and_cleans_up_provider_source(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector_service = ConnectorService(sessions, providers)
    created = await connector_service.create(_create_request())
    connection_secrets = _Secrets()
    connection_service = ConnectionService(sessions, providers, connection_secrets)
    connection = await connection_service.create_from_provider_result(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=created.revision.id,
        principal_ref=None,
        name="Shared GitHub",
        result=ConnectorProviderConnectionResult(
            provider_state_version="1",
            provider_state={},
            account=ConnectorProviderAccount(external_id="account-1", display_name="Account One"),
            secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("first-token")),),
        ),
        actor=_actor(),
    )
    trigger_secrets = _TriggerSecrets()
    service = TriggerService(sessions, providers, connection_secrets, trigger_secrets, _AgentTargets())
    trigger = await service.create(
        CreateTrigger(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            name="On push",
            principal_ref=_actor(),
            agent_revision_id="agrev_1",
            source=ConnectorEventTriggerSource(
                connector_revision_id=created.revision.id,
                connection_id=connection.id,
                event_type="push",
                provider_event_config_version="1",
                config={"branch": "main"},
            ),
            input_template={"event": "{{ event }}"},
            created_by=_actor(),
        )
    )
    active = await service.enable(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=1,
        context=ConnectorProviderContext(
            operation_id="op_enable",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        callback_url=f"https://foundation.example.test/api/v1/connector-events/{trigger.id}",
        actor=_actor(),
    )

    assert active.status is TriggerStatus.active
    assert active.version == 3
    assert trigger_secrets.values[trigger.id][0].value.get_secret_value() == "webhook-secret"

    provider = providers.require("test")
    assert isinstance(provider, _Provider)
    provider.fail_stop = True
    disabled = await service.disable(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=3,
        context=ConnectorProviderContext(
            operation_id="op_disable",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        actor=_actor(),
    )
    assert disabled.status is TriggerStatus.disabled
    assert disabled.version == 4
    assert trigger.id in trigger_secrets.values

    provider.fail_stop = False
    reconciled = await service.reconcile_event_source(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=4,
        context=ConnectorProviderContext(
            operation_id="op_reconcile",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        actor=_actor(),
    )
    assert reconciled.status is TriggerStatus.disabled
    assert reconciled.version == 5
    assert trigger.id not in trigger_secrets.values


@pytest.mark.anyio
async def test_disabling_unknown_activation_reconciles_original_operation_before_cleanup(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    created = await ConnectorService(sessions, providers).create(_create_request())
    connection_secrets = _Secrets()
    connection = await ConnectionService(sessions, providers, connection_secrets).create_from_provider_result(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=created.revision.id,
        principal_ref=None,
        name="Shared GitHub",
        result=_connection_result("access-token"),
        actor=_actor(),
    )
    trigger_secrets = _TriggerSecrets()
    service = TriggerService(sessions, providers, connection_secrets, trigger_secrets, _AgentTargets())
    trigger = await service.create(
        CreateTrigger(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            name="On push",
            principal_ref=_actor(),
            agent_revision_id="agrev_1",
            source=ConnectorEventTriggerSource(
                connector_revision_id=created.revision.id,
                connection_id=connection.id,
                event_type="push",
                provider_event_config_version="1",
                config={},
            ),
            input_template={"event": "{{ event }}"},
            created_by=_actor(),
        )
    )
    async with transaction(sessions) as session:
        record = await session.get(TriggerRecord, trigger.id)
        assert record is not None
        record.status = "activating"
        record.lifecycle_operation_id = "op_activation_unknown"
        record.version = 2

    disabled = await service.disable(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=2,
        context=ConnectorProviderContext(
            operation_id="op_disable_request",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        actor=_actor(),
    )
    reconciled = await service.reconcile_event_source(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=3,
        context=ConnectorProviderContext(
            operation_id="op_reconcile_request",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        actor=_actor(),
    )

    provider = providers.require("test")
    assert isinstance(provider, _Provider)
    assert disabled.status is TriggerStatus.disabled
    assert provider.last_reconcile_operation_id == "op_activation_unknown"
    assert provider.last_reconcile_had_source is False
    assert reconciled.status is TriggerStatus.disabled
    assert reconciled.version == 4


@pytest.mark.anyio
async def test_connector_event_ingress_verifies_expands_and_deduplicates_turn(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector = await ConnectorService(sessions, providers).create(_create_request())
    connection_secrets = _Secrets()
    connection = await ConnectionService(sessions, providers, connection_secrets).create_from_provider_result(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=connector.revision.id,
        principal_ref=None,
        name="Shared GitHub",
        result=ConnectorProviderConnectionResult(
            provider_state_version="1",
            provider_state={},
            account=ConnectorProviderAccount(external_id="account-1", display_name="Account One"),
            secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("first-token")),),
        ),
        actor=_actor(),
    )
    trigger_secrets = _TriggerSecrets()
    triggers = TriggerService(sessions, providers, connection_secrets, trigger_secrets, _AgentTargets())
    trigger = await triggers.create(
        CreateTrigger(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            name="On push",
            principal_ref=_actor(),
            agent_revision_id="agrev_1",
            source=ConnectorEventTriggerSource(
                connector_revision_id=connector.revision.id,
                connection_id=connection.id,
                event_type="push",
                provider_event_config_version="1",
                config={},
            ),
            input_template={"type": "{{ event.type }}", "payload": "{{ event.data }}"},
            created_by=_actor(),
        )
    )
    active = await triggers.enable(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=1,
        context=ConnectorProviderContext(
            operation_id="op_enable",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        callback_url=f"https://foundation.example.test/api/v1/connector-events/{trigger.id}",
        actor=_actor(),
    )
    turns = _TurnAcceptor()
    ingress = TriggerIngressService(sessions, providers, trigger_secrets, turns)
    context = ConnectorProviderContext(
        operation_id="op_webhook",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )

    first = await ingress.receive_webhook(
        trigger_id=active.id, headers={"x-signature": "signed"}, body=b"{}", context=context
    )
    duplicate = await ingress.receive_webhook(
        trigger_id=active.id,
        headers={"x-signature": "signed"},
        body=b"{}",
        context=context,
    )

    assert first[0].duplicate is False
    assert duplicate[0].duplicate is True
    assert duplicate[0].turn_id == first[0].turn_id
    assert len(turns.calls) == 1
    assert turns.calls[0]["input"] == {"type": "push", "payload": {}}


@pytest.mark.anyio
async def test_schedule_ingress_accepts_only_latest_missed_instant(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    triggers = TriggerService(sessions, providers, _Secrets(), _TriggerSecrets(), _AgentTargets())
    trigger = await triggers.create(
        CreateTrigger(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            name="Report",
            principal_ref=_actor(),
            agent_revision_id="agrev_1",
            source=ScheduleTriggerSource(type="interval", interval_seconds=60),
            input_template={"scheduled_at": "{{ scheduled_at }}"},
            created_by=_actor(),
        )
    )
    enabled_at = datetime.now(UTC)
    active = await triggers.enable(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        trigger_id=trigger.id,
        expected_version=1,
        context=ConnectorProviderContext(
            operation_id="op_enable",
            deadline=enabled_at + timedelta(seconds=30),
        ),
        callback_url="https://unused.example.test",
        actor=_actor(),
    )
    turns = _TurnAcceptor()
    ingress = TriggerIngressService(sessions, providers, _TriggerSecrets(), turns)
    after_downtime = enabled_at + timedelta(seconds=250)

    receipts = await ingress.accept_due_schedules(now=after_downtime)
    repeated = await ingress.accept_due_schedules(now=after_downtime)

    assert active.status is TriggerStatus.active
    assert len(receipts) == 1
    assert repeated == ()
    assert len(turns.calls) == 1
    accepted_at = datetime.fromisoformat(turns.calls[0]["input"]["scheduled_at"])
    assert after_downtime - timedelta(seconds=60) < accepted_at <= after_downtime


@pytest.mark.anyio
async def test_trigger_template_rejects_interpolation_and_wrong_source_placeholder(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    service = TriggerService(sessions, providers, _Secrets(), _TriggerSecrets(), _AgentTargets())

    with pytest.raises(ConnectorError) as rejected:
        await service.create(
            CreateTrigger(
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                name="Invalid",
                principal_ref=_actor(),
                agent_revision_id="agrev_1",
                source=ScheduleTriggerSource(type="interval", interval_seconds=60),
                input_template={"prompt": "run at {{ scheduled_at }}"},
                created_by=_actor(),
            )
        )

    assert rejected.value.code == "invalid_request"


@pytest.mark.anyio
async def test_connector_tools_freeze_provider_contract_and_dispatch_with_fresh_credentials(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector_service = ConnectorService(sessions, providers)
    created = await connector_service.create(_create_request())
    secrets = _Secrets()
    connection_service = ConnectionService(sessions, providers, secrets)
    connection = await connection_service.create_from_provider_result(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=created.revision.id,
        principal_ref=_actor(),
        name="Personal GitHub",
        result=ConnectorProviderConnectionResult(
            provider_state_version="1",
            provider_state={},
            account=ConnectorProviderAccount(external_id="account-1", display_name="Account One"),
            secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("runtime-token")),),
        ),
        actor=_actor(),
    )
    authorizer = _ToolAuthorizer()
    runtime = ConnectorProviderRuntime(sessions, providers, secrets, authorizer)
    context = ConnectorProviderContext(operation_id="op_tool", deadline=datetime.now(UTC) + timedelta(seconds=30))

    declaration = await runtime.create_declaration(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=created.revision.id,
        connection_id=connection.id,
        selected_provider_tool_names=("create_issue",),
        principal=_actor(),
        context=context,
    )
    selection = await runtime.prepare_turn_selection(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        declaration=declaration,
        declaration_index=0,
        principal=_actor(),
        context=context,
    )
    listed = await runtime.list_selected_tools(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_id=created.connector.id,
        connector_revision_id=selection.connector_revision_id,
        connection_id=selection.connection_id,
        effective_tools=selection.effective_tools,
        provider_contract_version=selection.provider_contract_version,
        principal=_actor(),
        context=context,
    )
    result = await runtime.call_tool(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_id=created.connector.id,
        connector_revision_id=selection.connector_revision_id,
        connection_id=selection.connection_id,
        effective_tools=selection.effective_tools,
        provider_contract_version=selection.provider_contract_version,
        provider_tool_name="create_issue",
        arguments={"title": "Runtime bug"},
        principal=_actor(),
        context=context,
    )

    provider = providers.require("test")
    assert isinstance(provider, _Provider)
    assert declaration.tools == ("create_issue",)
    assert declaration.provider_lock.contract_version == "1"
    assert selection.effective_tools == ("create_issue",)
    assert selection.provider_contract_version == "1"
    assert listed[0].parameters_json_schema["required"] == ["title"]
    assert result.value == {"created": "Runtime bug"}
    assert provider.last_call_had_secret is True
    assert authorizer.calls == 5


@pytest.mark.anyio
async def test_connector_tool_dispatch_fails_closed_for_dependency_lock_change(
    sessions: async_sessionmaker[AsyncSession],
    providers: ConnectorProviderCatalog,
) -> None:
    connector_service = ConnectorService(sessions, providers)
    created = await connector_service.create(_create_request())
    secrets = _Secrets()
    authorizer = _ToolAuthorizer()
    runtime = ConnectorProviderRuntime(sessions, providers, secrets, authorizer)
    context = ConnectorProviderContext(operation_id="op_tool", deadline=datetime.now(UTC) + timedelta(seconds=30))
    declaration = await runtime.create_declaration(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_revision_id=created.revision.id,
        connection_id=None,
        selected_provider_tool_names=("create_issue",),
        principal=_actor(),
        context=context,
    )
    incompatible = declaration.model_copy(
        update={
            "provider_lock": declaration.provider_lock.model_copy(update={"contract_version": "2"}),
        }
    )

    with pytest.raises(ConnectorError) as rejected:
        await runtime.call_tool(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_id=created.connector.id,
            connector_revision_id=created.revision.id,
            connection_id=None,
            effective_tools=("create_issue",),
            provider_contract_version=incompatible.provider_lock.contract_version,
            provider_tool_name="create_issue",
            arguments={"title": "Runtime bug"},
            principal=_actor(),
            context=context,
        )

    assert rejected.value.code == "provider_not_trusted"
    assert authorizer.calls == 1
