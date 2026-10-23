"""Vercel EIP and desktop authority: validate first, pin sessions, never repair on access."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.providers.environment.native.configuration import NamedTargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.http import NativeHTTP
from a13n_harness.providers.environment.vercel import http as eip
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.shared import (
    Route,
    SandboxResponse,
    VercelConnectionConfiguration,
    VercelEnvironmentConfiguration,
)
from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environments.vercel.configuration import VercelRecipe
from a13n_service.providers.environments.vercel.management import VercelManagement

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def recipe():
    return VercelRecipe(execution="envd_http", snapshot="snap_fixture", desktop=True, desktop_assistance=True)


def manager(config=None):
    config = config or recipe()
    state = EnvironmentState(
        provider_key="vercel",
        state_version="1",
        state=NamedTargetState(
            target_id="a13n-fixture",
            backing_id="a13n-fixture:1",
            environment_id="env-fixture",
            configuration_fingerprint=config.fingerprint,
        ).model_dump(mode="json"),
    )
    runtime = NativeRuntime(
        VercelConnectionConfiguration(team_id="team-fixture", project_id="project-fixture"),
        TokenCredential(api_key="private-cloud-token"),
    )
    return VercelManagement(config, "env-fixture", state, runtime, "op-fixture")


def response(manage, *, session="session-1", status="running"):
    return SandboxResponse.model_validate(
        {
            "sandbox": {
                "name": "a13n-fixture",
                "createdAt": 1,
                "persistent": True,
                "status": status,
                "tags": manage.labels,
            },
            "session": {"id": session, "status": status, "timeout": 3600000},
            "routes": [
                {"port": 8888, "url": "https://envd.vercel.run"},
                {"port": 6081, "url": "https://desktop.vercel.run"},
            ],
        }
    )


def install_transport(monkeypatch, handler):
    original = NativeHTTP.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.client = httpx2.AsyncClient(base_url="https://api.vercel.com", transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(NativeHTTP, "__init__", init)


def description():
    return {
        "protocol_version": 1,
        "generation": "a" * 32,
        "port": 6081,
        "max_ttl_seconds": 60,
        "read_only_enforced": True,
    }


async def test_native_fingerprint_and_desktop_opt_in():
    config = VercelEnvironmentConfiguration()
    old = config.model_dump_json(exclude={"execution", "snapshot", "desktop"})
    assert config.fingerprint == hashlib.sha256(old.encode()).hexdigest()
    assert VercelRecipe().fingerprint == config.fingerprint
    assert (
        recipe().fingerprint
        == VercelEnvironmentConfiguration.model_validate(
            recipe().model_dump(exclude={"desktop_assistance"})
        ).fingerprint
    )
    for values in ({"desktop": True}, {"execution": "envd_http"}, {"snapshot": "snap_x"}, {"desktop_assistance": True}):
        with pytest.raises(ValueError):
            VercelRecipe(**values)


@pytest.mark.parametrize("fault", ["owner", "replacement", "stopped", "absent"])
async def test_validation_precedes_guest_commands(monkeypatch, fault):
    manage = manager()
    calls = []

    async def handler(request):
        calls.append(request.method)
        if fault == "absent":
            return httpx2.Response(404)
        data = response(manage, status="stopped" if fault == "stopped" else "running").model_dump()
        if fault == "owner":
            data["sandbox"]["tags"] = {}
        if fault == "replacement":
            data["sandbox"]["createdAt"] = 2
        return httpx2.Response(200, json=data)

    install_transport(monkeypatch, handler)
    try:
        with pytest.raises(EnvironmentProviderError):
            await manage.desktop_access(access.Describe())
        assert calls == ["GET"]
    finally:
        await manage.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://desktop.vercel.run",
        "https://evil.example",
        "https://desktop.vercel.run.evil.example",
        "https://u:p@desktop.vercel.run",
        "https://desktop.vercel.run/?secret=x",
        "https://desktop.vercel.run/path",
        "https://desktop.vercel.run:8443",
        "https://desktop.vercel.run:invalid",
        "https://[broken.vercel.run",
    ],
)
async def test_routes_reject_unsafe_origins(url):
    manage = manager()
    manage.routes = [Route(port=6081, url=url)]
    with pytest.raises(EnvironmentProviderError):
        manage.endpoint(6081)


async def test_multiple_routes_rejected():
    manage = manager()
    manage.routes = [Route(port=8888, url="https://a.vercel.run")] * 2
    with pytest.raises(EnvironmentProviderError):
        manage.endpoint(8888)


async def test_desktop_commands_share_pinned_session_and_propagate_cleanup_failure(monkeypatch):
    manage = manager()
    calls = []
    requests = []

    async def lookup():
        calls.append("lookup")
        value = response(manage)
        manage.accept(value)
        return value

    async def execute(ref, session_id, argv, **kwargs):
        calls.append(session_id)
        requests.append(argv)
        if len(requests) == 1:
            return json.dumps({"result": description()})
        return '{"error":"input_cleanup_failed"}'

    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr("a13n_service.providers.environments.vercel.management.command", execute)

    class Policy:
        async def validate(self, url):
            assert url == "https://desktop.vercel.run"

    manage.backend._endpoint_policy = Policy()
    result = await manage.desktop_access(access.Describe())
    assert result.websocket_url == "wss://desktop.vercel.run/websockify"
    with pytest.raises(EnvironmentProviderError) as error:
        await manage.desktop_access(access.Settle(generation="a" * 32))
    assert error.value.code == "desktop_access_unconfirmed"
    assert calls == ["lookup", "session-1", "session-1"]
    with pytest.raises(EnvironmentProviderError):
        manage.accept(response(manage, session="session-2"))
    assert "private-cloud-token" not in repr(requests)


async def test_endpoint_policy_before_loading_credential_and_transport_closed(monkeypatch):
    manage = manager()
    events = []

    async def running():
        manage.accept(response(manage))
        return "session-1"

    async def closed():
        events.append("close")

    class Policy:
        async def validate(self, url):
            events.append("policy")
            raise EndpointPolicyError("denied")

    manage.backend._endpoint_policy = Policy()
    monkeypatch.setattr(manage, "running_session", running)
    monkeypatch.setattr(manage, "close_transport", closed)

    async def unexpected(*args, **kwargs):
        pytest.fail("must not read guest credentials")

    monkeypatch.setattr(eip, "command", unexpected)
    with pytest.raises(EnvironmentProviderError) as error:
        async with eip.http_session(manage):
            pass
    assert error.value.code == "envd_endpoint_denied"
    assert events == ["policy", "close"]


async def test_eip_uses_guest_token_and_closes_on_cancellation(monkeypatch):
    manage = manager()
    events = []

    async def running():
        manage.accept(response(manage))
        return "session-1"

    async def closed():
        events.append("close")

    async def execute(ref, session, argv, **kwargs):
        assert session == "session-1" and kwargs["sudo"]
        return "guest-only-token"

    @asynccontextmanager
    async def runtime(**kwargs):
        cred = await kwargs["load_credential"]()
        assert cred.token.get_secret_value() == "guest-only-token"
        assert not cred.headers

        class Runtime:
            @asynccontextmanager
            async def open_session(self, **options):
                assert options["expected_device_id"] == "env-fixture"
                assert "computer.observe" in options["required_methods"]
                assert options["working_directory"] == "/workspace"
                try:
                    yield object()
                finally:
                    events.append("session-close")

        yield Runtime()

    monkeypatch.setattr(manage, "running_session", running)
    monkeypatch.setattr(manage, "close_transport", closed)
    monkeypatch.setattr(eip, "command", execute)
    monkeypatch.setattr(eip, "managed_http_runtime", runtime)
    with pytest.raises(asyncio.CancelledError):
        async with eip.http_session(manage):
            raise asyncio.CancelledError
    assert events == ["session-close", "close"]


@pytest.mark.parametrize(
    "body",
    ['{"stream":"stdout","data":"too long"}', '{"command":{"exitCode":1}}', '{"broken":true}', '{"stream":"error"}'],
)
async def test_commands_do_not_replay_unknown_or_failed_output(monkeypatch, body):
    calls = []
    manage = manager()

    async def handler(request):
        calls.append(request)
        return httpx2.Response(200, content=body)

    install_transport(monkeypatch, handler)
    try:
        with pytest.raises(EnvironmentProviderError):
            await command(manage, "session-1", ["true"], timeout=1, max_output=3)
        assert len(calls) == 1
    finally:
        await manage.close()


@pytest.mark.parametrize("existing", ["running", "stopped", None])
async def test_lifecycle_starts_services_only_on_new_sessions(monkeypatch, existing):
    manage = manager()
    events = []
    if existing is None:
        manage.target = None

    async def lookup():
        return response(manage, status=existing) if existing else None

    class Transport:
        async def request(self, method, path, **kwargs):
            events.append((method, path, kwargs))
            return response(manage).model_dump(mode="json")

    async def ready(value, *, bootstrap):
        events.append(("ready", bootstrap))

    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr(manage, "transport", lambda: Transport())
    monkeypatch.setattr(manage, "_ready", ready)
    await manage.create()
    assert events[-1] == ("ready", existing != "running")
    if existing is None:
        assert events[0][1] == "/v3/sandboxes"
        body = events[0][2]["body"]
        assert body["source"] == {"type": "snapshot", "snapshotId": "snap_fixture"}
        assert body["ports"] == [8888, 6081] and "runtime" not in body
    elif existing == "running":
        assert len(events) == 1
