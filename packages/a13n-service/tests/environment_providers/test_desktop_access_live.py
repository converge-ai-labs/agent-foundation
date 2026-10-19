"""Opt-in raw-RFB enforcement tests on owned targets; never opens a user's desktop.

E2B requires A13N_TEST_E2B_ENVD_TEMPLATE with preinstalled Envd and access components.
Docker requires an image with the current desktop launcher and access dependencies.
Actual remote routing/TLS and Service Run handoff are separate acceptance gates.
"""

import asyncio
import json
import os
import shlex
import struct
import time
from uuid import uuid4

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime
from a13n_service.providers.environment import desktop_access as protocol
from a13n_service.providers.environments.docker.configuration import DockerRecipe
from a13n_service.providers.environments.docker.management import DockerManagement
from a13n_service.providers.environments.e2b.configuration import E2BRecipe
from a13n_service.providers.environments.e2b.management import E2BManagement
from pydantic import SecretStr


@pytest.fixture(params=["e2b", "docker"])
def desktop(request, tmp_path):
    identity = "env-access-" + uuid4().hex[:16]
    if request.param == "e2b":
        if os.environ.get("A13N_TEST_DESKTOP_ACCESS_E2B") != "1" or not os.environ.get("A13N_TEST_E2B_API_KEY"):
            pytest.skip("Requires explicit A13N_TEST_DESKTOP_ACCESS_E2B=1 and E2B credentials")
        from a13n_harness.providers.environment.e2b.connection import close_sandbox, open_sandbox

        template = os.environ.get("A13N_TEST_E2B_ENVD_TEMPLATE")
        if not template:
            pytest.skip("Requires A13N_TEST_E2B_ENVD_TEMPLATE")
        recipe = E2BRecipe(
            template=template,
            execution="envd_http",
            desktop_assistance=True,
            desktop=True,
            timeout_seconds=600,
            request_timeout_seconds=60,
        )
        management = E2BManagement(
            recipe,
            environment_id=identity,
            state=None,
            runtime=E2BProviderRuntime(SecretStr(os.environ["A13N_TEST_E2B_API_KEY"])),
            operation_id="op-access-test",
        )
        try:
            asyncio.run(management.create())
            assert management.state is not None

            async def attached_command(argv):
                sandbox = await open_sandbox(
                    management._state.sandbox_id, management._options(), management._validate_target
                )
                try:
                    return (await sandbox.commands.run(shlex.join(argv), user="root", timeout=25)).stdout
                finally:
                    await close_sandbox(sandbox)

            def command(argv):
                return asyncio.run(attached_command(argv))

            description = asyncio.run(management.desktop_access(protocol.Describe()))
            assert isinstance(description, protocol.Description) and description.revocation_fences_grants
            assert description.websocket_url
            yield (
                management,
                command,
                description.websocket_url,
                ["env", "DISPLAY=:99", "XAUTHORITY=/run/a13n-desktop/Xauthority"],
            )
        finally:
            if management.state is not None:
                recovery = tmp_path / "owned-e2b-target.json"
                recovery.write_text(
                    json.dumps({"environment_id": identity, "sandbox_id": management.state.state["sandbox_id"]})
                )
                for attempt in range(3):
                    try:
                        asyncio.run(management.destroy())
                        assert asyncio.run(management.inspect()) == "absent"
                        recovery.unlink()
                        break
                    except EnvironmentProviderError:
                        if attempt == 2:
                            raise
                        time.sleep(1)
    else:
        image = os.environ.get("A13N_TEST_DESKTOP_ACCESS_DOCKER_IMAGE")
        if not image:
            pytest.skip("Requires A13N_TEST_DESKTOP_ACCESS_DOCKER_IMAGE")
        import docker

        engine = docker.from_env()
        recipe = DockerRecipe(image=image, desktop=True, execution="envd_http", pull_policy="never")
        management = DockerManagement(recipe, identity, None, DockerProviderRuntime(DockerSDKEngine(engine)))
        try:
            box = engine.containers.run(
                image,
                detach=True,
                name="a13n-" + identity,
                labels=management.labels,
                environment={
                    "A13N_DESKTOP_ACCESS": "true",
                    "A13N_ENVD_TRANSPORT": "http",
                    "A13N_ENVD_HTTP_BIND": "0.0.0.0:8888",
                    "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
                },
                ports={"6081/tcp": ("127.0.0.1", None)},
                mem_limit="2g",
            )
            management._remember(box)

            def command(argv):
                result = box.exec_run(argv, user="root")
                assert result.exit_code == 0, "Guest command failed"
                return result.output.decode()

            box.reload()
            endpoint = (
                "ws://127.0.0.1:" + box.attrs["NetworkSettings"]["Ports"]["6081/tcp"][0]["HostPort"] + "/websockify"
            )
            yield management, command, endpoint, ["env", "DISPLAY=:99", "XAUTHORITY=/run/a13n-desktop/Xauthority"]
        finally:
            if box is not None:
                box.remove(force=True)
                with pytest.raises(docker.errors.NotFound):
                    engine.containers.get(box.id)
            engine.close()


@pytest.mark.timeout(300)
def test_readonly_controller_revocation_expiry_and_process_crash(desktop):
    from websockets.exceptions import ConnectionClosed, InvalidMessage, InvalidStatus

    from .desktop_access_rfb import RFB, AuthRejected, wait_cursor

    management, command, endpoint, environment = desktop
    clients = []

    def call(request):
        return asyncio.run(management.desktop_access(request))

    deadline = time.monotonic() + 20
    while True:
        try:
            description = call(protocol.Describe())
            assert isinstance(description, protocol.Description)
            break
        except EnvironmentProviderError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    generation = description.generation

    def grant(identity, mode, epoch, ttl=60):
        result = call(protocol.Grant(generation=generation, id=identity, mode=mode, epoch=epoch, ttl_seconds=ttl))
        assert isinstance(result, protocol.AccessGrant)
        return result

    def client(grant, *, retry_handshake=False):
        assert grant.token is not None and grant.password is not None
        for attempt in range(2):
            try:
                connection = RFB(
                    endpoint + "?token=" + grant.token.get_secret_value(), grant.password.get_secret_value()
                )
                clients.append(connection)
                return connection
            except (TimeoutError, ConnectionClosed):
                if not retry_handshake or attempt:
                    raise
                # RFB construction sends no GUI input. Reauthorize an existing live
                # grant before one transport retry; an expired grant must still fail.
                call(protocol.Renew(generation=generation, id=grant.id, epoch=grant.epoch))
        raise AssertionError("Handshake did not complete")

    def cursor():
        values = dict(
            line.split("=", 1)
            for line in command([*environment, "xdotool", "getmouselocation", "--shell"]).strip().splitlines()
        )
        return int(values["X"]), int(values["Y"])

    def input_state():
        # Query the actual X server, not the management endpoint's reported cleanup flag.
        code = """import ctypes,json
x=ctypes.CDLL("libX11.so.6"); p=ctypes.c_void_p; w=ctypes.c_ulong; i=ctypes.c_int
x.XOpenDisplay.argtypes=[ctypes.c_char_p];x.XOpenDisplay.restype=p
x.XDefaultRootWindow.argtypes=[p];x.XDefaultRootWindow.restype=w
x.XQueryKeymap.argtypes=[p,p]
x.XQueryPointer.argtypes=[p,w,ctypes.POINTER(w),ctypes.POINTER(w),*[ctypes.POINTER(i)]*4,ctypes.POINTER(ctypes.c_uint)]
d=x.XOpenDisplay(None);assert d
keys=ctypes.create_string_buffer(32);x.XQueryKeymap(d,keys)
r,c=w(),w();coords=[i() for _ in range(4)];mask=ctypes.c_uint()
assert x.XQueryPointer(d,x.XDefaultRootWindow(d),ctypes.byref(r),ctypes.byref(c),*(ctypes.byref(v) for v in coords),ctypes.byref(mask))
print(json.dumps([any(keys.raw),bool(mask.value & 0x1f00)]))
"""
        return json.loads(command([*environment, "python3", "-I", "-c", code]))

    try:
        observer = grant("view_first", "view", 1)
        view = client(observer, retry_handshake=True)
        assert view.frame()["pixel_bytes"] > 0
        with pytest.raises((AuthRejected, ConnectionClosed)):
            client(observer.model_copy(update={"password": SecretStr("invalid!")}))
        command([*environment, "xdotool", "mousemove", "10", "10"])
        view.pointer(101, 103)
        view.send(struct.pack("!BBHI", 4, 1, 0, 0xFFE1))
        view.frame()
        assert cursor() == (10, 10)
        assert input_state() == [False, False]
        call(protocol.Renew(generation=generation, id="view_first", epoch=1))
        controller = grant("control_first", "control", 2)
        control = client(controller, retry_handshake=True)
        control.frame()
        control.pointer(201, 203)
        control.frame()
        assert wait_cursor(cursor, (201, 203))
        call(protocol.Renew(generation=generation, id="control_first", epoch=2))
        call(protocol.Renew(generation=generation, id="view_first", epoch=1))
        assert grant("control_first", "control", 2).token == controller.token
        with pytest.raises(EnvironmentProviderError) as busy:
            grant("control_second", "control", 3)
        assert busy.value.code == "desktop_access_controller_exists"
        control.send(struct.pack("!BBHI", 4, 1, 0, 0xFFE1))
        control.send(struct.pack("!BBHH", 5, 1, 201, 203))
        control.frame()
        assert input_state() == [True, True]
        revoked = call(protocol.Inspect(operation="revoke", generation=generation, id="control_first", epoch=2))
        assert isinstance(revoked, protocol.AccessGrant) and revoked.state == "revoked"
        assert input_state() == [False, False]
        assert control.closed()
        with pytest.raises((ConnectionClosed, InvalidMessage, InvalidStatus, EOFError, AuthRejected)):
            client(controller)
        call(protocol.Renew(generation=generation, id="view_first", epoch=1))
        assert view.frame()["pixel_bytes"] >= 0
        assert grant("control_first", "control", 2).token is None
        with pytest.raises(EnvironmentProviderError):
            grant("control_stale", "control", 2)
        short = grant("control_short", "control", 4)
        expiring = client(short, retry_handshake=True)
        expiring.frame()
        expiring.send(struct.pack("!BBHI", 4, 1, 0, 0xFFE1))
        expiring.frame()
        call(protocol.Renew(generation=generation, id="control_short", epoch=4, ttl_seconds=3))
        time.sleep(5)
        assert expiring.closed()
        assert input_state() == [False, False]
        with pytest.raises((ConnectionClosed, InvalidMessage, InvalidStatus, EOFError, AuthRejected)):
            client(short)
        with pytest.raises(EnvironmentProviderError):
            call(protocol.Renew(generation=generation, id="control_short", epoch=4))
        assert isinstance(call(protocol.Settle(generation=generation)), protocol.Settlement)
        crash = client(grant("control_crash", "control", 5), retry_handshake=True)
        crash.frame()
        pid = int(command(["sh", "-c", "pgrep -f '^python3 -I /opt/a13n-desktop-access.py serve ' | head -1"]).strip())
        command(["kill", "-KILL", str(pid)])
        assert crash.closed()
    finally:
        for connection in clients:
            connection.close()
