"""Socket wire ACKs, app routing, and database ownership boundaries."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_service.connectivity.accounts.domain import CreateAccountRequest, UpdateAccountRequest
from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord
from a13n_service.connectivity.providers.lark.frame_pb2 import Frame, Header
from a13n_service.connectivity.transports import clients
from a13n_service.connectivity.transports.configuration import connection_key, validate_credentials
from a13n_service.connectivity.transports.leases import ConnectionLeases
from a13n_service.connectivity.transports.models import EventConnectionRecord
from a13n_service.connectivity.transports.supervisor import _matches, _normalize
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from sqlalchemy import func, select

from .conftest import AGENT_ID, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor
from .test_slack import _config, _event_payload, _registry, _signed_request


def test_transport_credentials_and_connection_identity():
    with pytest.raises(NativeError, match="signing_secret"):
        validate_credentials("slack", _config(), {"bot_token": "test"})
    config = {**_config(), "event_transport": "websocket"}
    validate_credentials("slack", config, {"bot_token": "test", "app_token": "test-app"})
    with pytest.raises(NativeError, match="app_token"):
        validate_credentials("slack", config, {"bot_token": "test", "signing_secret": "old"})
    assert connection_key("slack", config) == connection_key("slack", {**config, "team_id": "T_OTHER"})
    assert connection_key("slack", config) != connection_key("slack", {**config, "api_app_id": "A_OTHER"})


@pytest.mark.parametrize(
    "url",
    [
        "ws://wss-primary.slack.com",
        "wss://slack.com.evil.test",
        "wss://127.0.0.1",
        "wss://user@slack.com",
        "wss://slack.com:8080",
    ],
)
def test_reject_untrusted_socket_endpoint(url):
    with pytest.raises(clients.TransportError):
        clients.socket_url(url, "slack")


def _frame(payload: bytes, *, sequence=0, total=1):
    return Frame(
        SeqID=1,
        LogID=1,
        service=1,
        method=1,
        payload=payload,
        headers=[
            Header(key="type", value="event"),
            Header(key="message_id", value="event"),
            Header(key="sum", value=str(total)),
            Header(key="seq", value=str(sequence)),
        ],
    )


def test_feishu_fragment_reassembly_retries_and_bounds():
    fragments = clients.Fragments()
    assert fragments.assemble(_frame(b"second", sequence=1, total=2)) is None
    assert fragments.assemble(_frame(b"second", sequence=1, total=2)) is None
    assert fragments.assemble(_frame(b"first", sequence=0, total=2)) == b"firstsecond"
    with pytest.raises(clients.TransportError):
        fragments.assemble(_frame(b"bad", sequence=5, total=2))
    with pytest.raises(clients.TransportError):
        fragments.assemble(_frame(b"bad", total=129))


class Socket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration from None

    async def recv(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise RuntimeError("end of fixture") from None

    async def send(self, message):
        self.sent.append(message)


def _connect(monkeypatch, socket):
    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield socket

    monkeypatch.setattr(clients, "FixedEndpointConnect", connect)
    monkeypatch.setattr(clients, "_validate_endpoint", AsyncMock())


async def test_slack_ack_follows_admission_and_storage_failure_is_not_acked(monkeypatch):
    payload = json.loads(_event_payload())
    socket = Socket(
        [
            json.dumps({"type": "hello", "connection_info": {"app_id": "A123"}}),
            json.dumps({"type": "events_api", "envelope_id": "envelope", "payload": payload}),
        ]
    )
    _connect(monkeypatch, socket)
    monkeypatch.setattr(
        clients, "_post", AsyncMock(return_value={"ok": True, "url": "wss://wss-primary.slack.com/link"})
    )
    admitted = asyncio.Event()
    release = asyncio.Event()

    async def admit(value):
        assert value == payload
        admitted.set()
        await release.wait()

    async with httpx2.AsyncClient() as client:
        task = asyncio.create_task(
            clients.slack_connection(client, token="test", app_id="A123", admit=admit, connected=AsyncMock())
        )
        await admitted.wait()
        assert not socket.sent
        release.set()
        await task
        assert json.loads(socket.sent[0]) == {"envelope_id": "envelope"}
        socket.messages = iter(
            [
                json.dumps({"type": "hello", "connection_info": {"app_id": "A123"}}),
                json.dumps({"type": "events_api", "envelope_id": "failed", "payload": payload}),
            ]
        )
        with pytest.raises(RuntimeError):
            await clients.slack_connection(
                client,
                token="test",
                app_id="A123",
                admit=AsyncMock(side_effect=RuntimeError("database down")),
                connected=AsyncMock(),
            )
        assert len(socket.sent) == 1


async def test_feishu_success_frame_only_after_admission(monkeypatch):
    socket = Socket([_frame(b'{"schema":"2.0"}').SerializeToString()])
    _connect(monkeypatch, socket)
    monkeypatch.setattr(
        clients,
        "_post",
        AsyncMock(return_value={"code": 0, "data": {"URL": "wss://msg-frontier.feishu.cn/ws?service_id=1"}}),
    )

    async def admit(value):
        assert value == {"schema": "2.0"}
        assert len(socket.sent) == 1  # only the initial protocol ping

    async with httpx2.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="end of fixture"):
            await clients.lark_connection(
                client, origin="https://open.feishu.cn", app_id="app", secret="test", admit=admit, connected=AsyncMock()
            )
    assert json.loads(Frame.FromString(socket.sent[1]).payload) == {"code": 200}


async def test_socket_admission_dedup_fencing_switch_and_status(connectivity_sessions, credential_protector):
    registry = _registry()
    accounts = AccountService(connectivity_sessions, registry, credential_protector)
    account = await accounts.create_account(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="socket-account",
        request=CreateAccountRequest(
            name="Socket",
            provider_key="slack",
            provider_config_version="slack_http_v1",
            provider_config={**_config(), "event_transport": "websocket"},
            credentials={"bot_token": "test", "app_token": "test-app", "signing_secret": "signing-secret"},
            receive_enabled=True,
            default_agent_id=AGENT_ID,
            execution_service_account_id=SERVICE_ACCOUNT_ID,
        ),
    )
    ingress = IngressEventService(
        connectivity_sessions,
        registry,
        credential_protector,
        request_max_bytes=1048576,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1048576,
        account_pending_max_count=100,
        account_pending_max_bytes=1048576,
        batch_max_bytes=1048576,
        dedup_horizon_seconds=3600,
    )
    snapshot, _ = await ingress.load_socket_account(account.id)
    key = connection_key("slack", account.provider_config)
    first, second = ConnectionLeases(connectivity_sessions, "first"), ConnectionLeases(connectivity_sessions, "second")
    claim = await first.claim(key, {account.id: account.version})
    assert claim is not None
    assert await second.claim(key, {account.id: account.version}) is None
    await first.update(claim, state="connected")
    assert (await accounts.event_connection(actor=actor(), account_id=account.id)).state == "connected"
    payload = json.loads(_event_payload())
    assert _matches(snapshot, payload)
    assert not _matches(snapshot, {**payload, "team_id": "another"})
    for _ in range(2):
        await ingress.receive_socket(snapshot=snapshot, decision=_normalize(snapshot, payload), claim=claim)
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 1
    assert (await ingress.receive(account_id=account.id, request=_signed_request(_event_payload()))).status_code == 404
    async with transaction(connectivity_sessions) as session:
        record = await session.get(EventConnectionRecord, key)
        record.lease_expires_at = utc_now() - timedelta(seconds=1)
    successor = await second.claim(key, {account.id: account.version})
    assert successor is not None and successor.generation > claim.generation
    with pytest.raises(NativeError, match="ownership expired"):
        await ingress.receive_socket(snapshot=snapshot, decision=_normalize(snapshot, payload), claim=claim)
    await first.release(claim)
    await second.update(successor, state="connected")
    updated = await accounts.update_account(
        actor=actor(),
        account_id=account.id,
        request=UpdateAccountRequest(
            expected_version=account.version, provider_config={**_config(), "event_transport": "http"}
        ),
    )
    assert updated.id == account.id and updated.default_agent_id == account.default_agent_id
    assert (await accounts.event_connection(actor=actor(), account_id=account.id)).state == "http"
    response = await ingress.receive(
        account_id=account.id, request=_signed_request(_event_payload(), timestamp=int(utc_now().timestamp()))
    )
    assert response.status_code == 200
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 1
    with pytest.raises(NativeError, match="Account changed"):
        await ingress.receive_socket(snapshot=snapshot, decision=_normalize(snapshot, payload), claim=successor)


async def test_ownership_loss_cancels_socket_and_releases_claim(monkeypatch):
    from a13n_service.connectivity.transports import supervisor
    from a13n_service.connectivity.transports.leases import ConnectionClaim

    running = asyncio.Event()
    closed = asyncio.Event()

    async def connection(*args):
        running.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    manager = supervisor.EventConnectionSupervisor(None, None, owner="owner")
    manager._reconnect = connection
    manager.leases.update = AsyncMock(side_effect=RuntimeError("database down"))
    manager.leases.release = AsyncMock()
    monkeypatch.setattr(supervisor, "RENEW_INTERVAL_SECONDS", 0.01)
    claim = ConnectionClaim("app", "owner", 1)
    await asyncio.wait_for(manager._owned_connection(None, claim, ()), timeout=1)
    assert running.is_set() and closed.is_set()
    manager.leases.release.assert_awaited_once_with(claim)


async def test_feishu_storage_failure_sends_no_success_ack(monkeypatch):
    socket = Socket([_frame(b'{"schema":"2.0"}').SerializeToString()])
    _connect(monkeypatch, socket)
    monkeypatch.setattr(
        clients,
        "_post",
        AsyncMock(return_value={"code": 0, "data": {"URL": "wss://msg-frontier.feishu.cn/ws?service_id=1"}}),
    )
    async with httpx2.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="storage"):
            await clients.lark_connection(
                client,
                origin="https://open.feishu.cn",
                app_id="app",
                secret="test",
                admit=AsyncMock(side_effect=RuntimeError("storage")),
                connected=AsyncMock(),
            )
    assert len(socket.sent) == 1 and Frame.FromString(socket.sent[0]).method == 0


async def test_drain_interrupts_connection_before_shutdown_wait():
    from a13n_service.connectivity.transports.supervisor import EventConnectionSupervisor

    manager = EventConnectionSupervisor(None, None, owner="owner")
    stopped = asyncio.Event()

    async def socket():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(socket())
    await asyncio.sleep(0)
    manager._running["app"] = ((), task)
    manager.drain()
    await asyncio.gather(task, return_exceptions=True)
    assert manager.is_draining() and stopped.is_set()
