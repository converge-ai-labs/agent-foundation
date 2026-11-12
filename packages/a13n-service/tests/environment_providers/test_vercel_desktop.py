"""Vercel EIP and desktop authority: validate first, pin sessions, never repair on access."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

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
from a13n_service.providers.environment._backend_factory import BackendProvider, ManagementBackendFactory
from a13n_service.providers.environment.management import Pending
from a13n_service.providers.environment.recipes.vercel import VercelRecipe
from a13n_service.providers.environments.vercel import preparation
from a13n_service.providers.environments.vercel.management import VercelManagement

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def recipe():
    return VercelRecipe(
        execution="envd_http",
        snapshot="snap_fixture",
        desktop=True,
        desktop_assistance=True,
        startup_command=["/usr/local/bin/start-envd"],
    )


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
        "protocol_version": 2,
        "generation": "a" * 32,
        "port": 6081,
        "max_ttl_seconds": 60,
        "read_only_enforced": True,
    }


async def test_native_fingerprint_and_desktop_opt_in():
    config = VercelEnvironmentConfiguration()
    old = config.model_dump_json(exclude={"execution", "snapshot", "desktop", "environment"})
    assert config.fingerprint == hashlib.sha256(old.encode()).hexdigest()
    assert VercelRecipe().fingerprint == config.fingerprint
    assert (
        recipe().fingerprint
        == VercelEnvironmentConfiguration.model_validate(
            recipe().model_dump(exclude={"desktop_assistance", "envd_management", "startup_command"})
        ).fingerprint
    )
    assert VercelRecipe(desktop=True).snapshot == "auto"
    assert VercelRecipe(execution="envd_http").snapshot == "auto"
    assert not VercelRecipe(execution="envd_http").desktop_assistance
    for values in ({"snapshot": "snap_x"}, {"desktop_assistance": True}):
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
                assert "working_directory" not in options
                assert options.get("configuration") is None
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
async def test_lifecycle_reconciles_startup_on_each_management_entry(monkeypatch, existing):
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

    async def ready(value):
        events.append(("ready", value.session.id))

    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr(manage, "transport", lambda: Transport())
    monkeypatch.setattr(manage, "_ready", ready)
    await manage.create()
    assert events[-1] == ("ready", "session-1")
    if existing is None:
        assert events[0][1] == "/v3/sandboxes"
        body = events[0][2]["body"]
        assert body["source"] == {"type": "snapshot", "snapshotId": "snap_fixture"}
        assert body["ports"] == [8888, 6081] and "runtime" not in body
    elif existing == "running":
        assert len(events) == 1


@pytest.mark.parametrize("execution", ["envd_http", "envd_ws"])
async def test_automatic_allocation_publishes_final_identity_before_install(monkeypatch, execution):
    manage = manager(VercelRecipe(execution=execution))
    manage.target = None
    calls = []

    async def lookup():
        return None

    class Transport:
        async def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            value = response(manage).model_dump(mode="json")
            value["sandbox"]["tags"] = kwargs["body"]["tags"]
            return value

    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr(manage, "transport", lambda: Transport())
    install = AsyncMock(side_effect=AssertionError("install before checkpoint"))
    monkeypatch.setattr(preparation, "prepare", install)
    result = await manage.create()
    assert isinstance(result, Pending) and result.settled and result.state == manage.state
    assert result.state.state["backing_id"] == "a13n-fixture:1"
    assert len(calls) == 1
    method, path, kwargs = calls[0]
    assert method == "POST" and path == "/v3/sandboxes"
    body = kwargs["body"]
    assert body["persistent"] and body["image"] == "vercel/sandbox/ubuntu"
    assert "source" not in body
    assert body["ports"] == ([8888] if execution == "envd_http" else [])
    assert body["tags"][preparation.TAG] == preparation.revision(desktop=False)
    install.assert_not_called()


@pytest.mark.parametrize("prepared", [False, True])
async def test_rejoins_same_target_and_prepares_before_readiness(monkeypatch, prepared):
    manage = manager(VercelRecipe(execution="envd_http"))
    value = response(manage)
    events = []

    async def lookup():
        manage.accept(value)
        return value

    async def prepare(ref, observed, operation_id):
        assert ref is manage and observed == value and operation_id == "op-fixture"
        events.append("prepare")
        return prepared

    async def ready(observed):
        events.append("ready")

    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr(preparation, "prepare", prepare)
    monkeypatch.setattr(manage, "_ready", ready)
    result = await manage.create()
    assert events == (["prepare", "ready"] if prepared else ["prepare"])
    assert (result is None) if prepared else isinstance(result, Pending)


@pytest.mark.parametrize("transition", ["create", "start"])
async def test_backend_preserves_pending_and_does_not_wait_for_websocket(monkeypatch, transition):
    manage = manager(VercelRecipe(execution="envd_http"))
    pending = Pending(manage.state, retry_after_seconds=3, settled=True)
    monkeypatch.setattr(manage, transition, AsyncMock(return_value=pending))
    factory = ManagementBackendFactory(key="vercel", environment_model=VercelRecipe, management=lambda **_: manage)
    provider = BackendProvider(factory, None, None)
    wait = AsyncMock(side_effect=AssertionError("waited for pending preparation"))
    monkeypatch.setattr(provider, "_wait_ready", wait)
    result = await getattr(provider, transition)(
        manage.recipe, environment_id="env-fixture", operation_id="op-fixture", state=manage.state
    )
    assert result == pending
    wait.assert_not_called()


@pytest.mark.parametrize("existing", ["running", "stopped", None])
@pytest.mark.parametrize("execution", ["envd_http", "envd_ws"])
async def test_preinstalled_snapshot_rejoins_authored_startup_before_readiness(monkeypatch, existing, execution):
    from a13n_service.providers.environments.vercel import management as module

    authored = ["/opt/team/start-envd", "--background"]
    manage = manager(
        VercelRecipe(
            execution=execution,
            snapshot="snap_custom",
            envd_management="preinstalled",
            startup_command=authored,
        )
    )
    if existing is None:
        manage.target = None
    running = existing == "running"
    events = []

    async def lookup():
        return response(manage, status=existing) if existing else None

    class Transport:
        async def request(self, method, path, **kwargs):
            return response(manage, session="session-new").model_dump(mode="json")

    async def launch(ref, session_id, argv, **kwargs):
        nonlocal running
        import base64
        import re

        assert ref is manage
        assert session_id == ("session-1" if existing == "running" else "session-new")
        assert argv[:2] == ["sh", "-c"]
        encoded = re.search(r"printf %s ([A-Za-z0-9+/=]+) \| base64 -d > install.sh", argv[2])
        assert encoded is not None
        assert base64.b64decode(encoded.group(1)).decode() == "exec 9>&-\nexec /opt/team/start-envd --background"
        assert kwargs["sudo"] is True
        assert kwargs["env"]["A13N_ENVD_DEVICE_ID"] == manage.owner
        events.append("authored-startup")
        running = True
        return "ready"

    async def readiness(*args, **kwargs):
        assert running, "a filesystem snapshot does not preserve the old daemon"
        events.append("authenticated-readiness")

    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr(manage, "transport", lambda: Transport())
    monkeypatch.setattr(module, "command", launch)
    monkeypatch.setattr(module, "wait_until_ready", readiness)
    await manage.create()
    assert events == ["authored-startup"] + (["authenticated-readiness"] if execution == "envd_http" else [])


@pytest.mark.parametrize(
    "values",
    [
        {"execution": "envd_http", "snapshot": "snap_custom"},
        {"execution": "envd_http", "startup_command": ["start-envd"]},
        {"execution": "envd_http", "envd_management": "managed", "startup_command": ["start-envd"]},
        {"execution": "native", "startup_command": ["start-envd"]},
    ],
)
def test_startup_command_requires_preinstalled_custom_snapshot(values):
    with pytest.raises(ValueError):
        VercelRecipe.model_validate(values)


@pytest.mark.parametrize("lost", ["allocation", "resume", "startup"])
async def test_authored_startup_reconciles_lost_replies_without_replaying_the_command(monkeypatch, tmp_path, lost):
    from a13n_service.providers.environments.vercel import management as module

    evidence = tmp_path / "effects"
    # A background child must not retain the dispatch lock after the authored launcher returns.
    authored = ["sh", "-c", f"printf once >> {evidence}; sleep 2 >/dev/null 2>&1 &"]
    manage = manager(VercelRecipe(execution="envd_http", snapshot="snap_team", startup_command=authored))
    manage.target = None if lost == "allocation" else manage.target
    status = None if lost == "allocation" else "stopped" if lost == "resume" else "running"
    lose_response = True
    dispatched = []
    readiness = AsyncMock()

    async def lookup():
        if status is None:
            return None
        value = response(manage, session="session-new", status=status)
        manage.accept(value)
        return value

    class Transport:
        async def request(self, method, path, **kwargs):
            nonlocal status, lose_response
            status = "running"
            if lose_response and lost in {"allocation", "resume"}:
                lose_response = False
                raise TimeoutError("lost provider reply")
            return response(manage, session="session-new").model_dump(mode="json")

    async def launch(ref, session_id, argv, **kwargs):
        nonlocal lose_response
        assert session_id == "session-new"
        script = argv[2].replace("/var/lib/a13n-startup/", str(tmp_path / "jobs") + "/")
        dispatched.append(script)
        process = await asyncio.create_subprocess_exec("sh", "-c", script, stdout=asyncio.subprocess.PIPE)
        output, _ = await process.communicate()
        assert process.returncode == 0
        if lose_response and lost == "startup":
            lose_response = False
            raise TimeoutError("lost guest reply")
        return output.decode()

    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr(manage, "transport", lambda: Transport())
    monkeypatch.setattr(module, "command", launch)
    monkeypatch.setattr(module, "wait_until_ready", readiness)
    with pytest.raises(TimeoutError):
        await manage.create()
    async with asyncio.timeout(5):
        while isinstance(await manage.create(), Pending):
            readiness.assert_not_awaited()
            await asyncio.sleep(0.01)
    assert await manage.create() is None
    assert evidence.read_text() == "once"
    assert len(set(dispatched)) == 1
    assert readiness.await_count == 2
