"""Disposable native adapters and independent filesystem evidence for conformance tests."""

import asyncio
import hashlib
import logging
import os
import secrets
import stat
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import uuid4

import anyio
from a13n_environment import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderRuntime,
    EnvironmentProviderError,
    EnvironmentState,
    HttpEnvdBackendConfiguration,
    HttpEnvdCredential,
    HttpEnvdEnvironmentProvider,
    HttpEnvdProviderRuntime,
    LocalEnvdEnvironmentProvider,
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    WebSocketEnvdConnections,
    WebSocketEnvdEnvironmentProvider,
    WebSocketEnvdProviderRuntime,
)
from pydantic import SecretStr
from websockets.asyncio.server import serve

from ..infrastructure.round_two_lab import REPOSITORY, free_origin, private_json
from ..infrastructure.tcp_proxy import TCPProxy

KINDS = ("direct-local", "local-envd", "http-envd", "websocket-envd")
logger = logging.getLogger(__name__)


def snapshot(root):
    """Record native contents and hidden candidates without following symlinks."""
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        value = [stat.S_IFMT(metadata.st_mode), stat.S_IMODE(metadata.st_mode)]
        if stat.S_ISLNK(metadata.st_mode):
            value.append(os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            value.append(hashlib.sha256(path.read_bytes()).hexdigest())
        result[str(path.relative_to(root))] = value
    return result


class FileBackend:
    def __init__(self, kind, directory, *, read_only=False, commands=False, network_faults=False):
        self.kind, self.directory, self.read_only = kind, directory, read_only
        self.commands = commands
        self.network_faults = network_faults
        self.proxy = None
        self.identity = "env-" + uuid4().hex
        self.root = directory / "workspace"
        self.outside = str(directory / "outside")
        self.process = None
        self.adapters = []
        self.configuration = None
        self.state = None

    @asynccontextmanager
    async def open(self, *, prepare=True):
        self.root.mkdir(mode=0o777)
        self.root.chmod(0o777)
        base = self.root / "file-tests"
        base.mkdir(mode=0o777)
        base.chmod(0o777)
        (base / "directory").mkdir(mode=0o777)
        (base / "source").write_text("ORIGINAL\n")
        (base / "source").chmod(0o666)
        async with AsyncExitStack() as stack:
            if self.kind == "direct-local":
                self.provider = DirectLocalEnvironmentProvider()
                configuration = {"root": {"path": str(self.root)}}
                self.runtime = DirectLocalProviderRuntime()
            elif self.kind == "local-envd":
                self.provider = LocalEnvdEnvironmentProvider()
                configuration = {"workspace": {"path": str(self.root), "read_only": self.read_only}}
                self.runtime = LocalEnvdProviderRuntime(
                    executable=self.binary(),
                    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=self.directory),
                )
            else:
                configuration = {}
                token = secrets.token_urlsafe(24)
                if self.kind == "http-envd":
                    self.provider = HttpEnvdEnvironmentProvider()
                    origin = free_origin()
                    endpoint = origin
                    if self.network_faults:
                        self.proxy = await stack.enter_async_context(
                            TCPProxy("127.0.0.1", int(origin.rsplit(":", 1)[1])).listen()
                        )
                        endpoint = f"http://127.0.0.1:{self.proxy.local_port}"
                    self.runtime = HttpEnvdProviderRuntime(
                        HttpEnvdBackendConfiguration(endpoint=endpoint, request_timeout=5),
                        HttpEnvdCredential(token=SecretStr(token)),
                    )
                else:
                    self.provider = WebSocketEnvdEnvironmentProvider()
                    hub = await stack.enter_async_context(WebSocketEnvdConnections())

                    async def attach(connection):
                        try:
                            await hub.attach(self.identity, connection)
                        except EnvironmentProviderError:
                            # The fixture owns daemon disconnect/restart, including abrupt exit.
                            logger.info("File fixture reverse connection detached environment=%s", self.identity)

                    def authorize(connection, request):
                        if request.headers.get("Authorization") != "Bearer " + token:
                            return connection.respond(401, "unauthorized")
                        return None

                    server = await stack.enter_async_context(
                        serve(
                            attach,
                            "127.0.0.1",
                            0,
                            subprotocols=["eip.v1"],
                            process_request=authorize,
                            compression=None,
                        )
                    )
                    origin = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/envd"
                    if self.network_faults:
                        self.proxy = await stack.enter_async_context(
                            TCPProxy("127.0.0.1", server.sockets[0].getsockname()[1]).listen()
                        )
                        origin = f"ws://127.0.0.1:{self.proxy.local_port}/envd"
                    self.runtime = WebSocketEnvdProviderRuntime(hub)
                self.state = EnvironmentState(
                    provider_key=self.provider.key,
                    state_version="1",
                    state={"daemon_environment_id": self.identity},
                )
                await self.start_daemon(origin, token, stack)
            if self.commands and self.kind in {"direct-local", "local-envd"}:
                configuration["shell_profiles"] = [
                    {"profile_id": "default", "executable": "/bin/bash", "fixed_arguments": ["-c"]}
                ]
                if self.kind != "direct-local":
                    configuration["trusted_executable_roots"] = ["/bin", "/usr/bin"]
            self.configuration = self.provider.validate_configuration(schema_version="1", value=configuration)
            try:
                self.environment = self.adapter()
                if prepare:
                    await self.prepare(self.environment)
                Path(self.outside).mkdir()
                (Path(self.outside) / "sentinel").write_text("OUTSIDE_UNCHANGED")
                yield self
            finally:
                with anyio.CancelScope(shield=True), anyio.fail_after(90):
                    errors = []
                    for adapter in reversed(self.adapters):
                        try:
                            await adapter.close()
                        except Exception as error:
                            errors.append(error)
                    if errors:
                        raise ExceptionGroup("File fixture cleanup failed", errors)

    @staticmethod
    def binary():
        path = Path(os.environ.get("A13N_ENVD_TEST_BINARY", REPOSITORY / "target/debug/a13n-envd")).resolve()
        assert path.is_file(), "Build a13n-envd or set A13N_ENVD_TEST_BINARY"
        return path

    def adapter(self, *, state=None, runtime=None):
        environment = self.provider.create_environment(
            environment_id=self.identity,
            configuration=self.configuration,
            state=state if state is not None else self.state,
            runtime=runtime or self.runtime,
        )
        self.adapters.append(environment)
        return environment

    async def prepare(self, environment):
        await environment.enter(thread_id="files", run_id="run", agent_instance_id="agent", mount_id="workspace")
        await environment.prepare()
        return environment

    async def start_daemon(self, origin, token, stack):
        runtime = self.directory / "runtime"
        runtime.mkdir(mode=0o700)
        config = self.directory / "envd.json"
        private_json(
            config,
            {
                "root_mount_id": "workspace",
                "mounts": [
                    {
                        "mount_id": "workspace",
                        "native_root": str(self.root),
                        "writable": not self.read_only,
                        "allow_command_execution": self.commands,
                        "max_file_bytes": 1024 * 1024,
                    }
                ],
                "shell_profiles": [
                    {
                        "profile_id": "default",
                        "display_name": "Fixture",
                        "native_executable": "/bin/bash",
                        "fixed_arguments": ["-c"],
                        "safe_base_environment": {},
                        "executable_search_roots": ["/bin", "/usr/bin"],
                        "max_script_bytes": 1048576,
                        "allow_login_mode": False,
                    }
                ]
                if self.commands
                else [],
            },
        )
        credential = self.directory / "token"
        credential.write_text(token)
        credential.chmod(0o600)
        environment = {
            "A13N_ENVD_ENVIRONMENT_ID": self.identity,
            "A13N_ENVD_RUNTIME_DIR": str(runtime),
            "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
        }
        if self.kind == "http-envd":
            environment.update(
                A13N_ENVD_TRANSPORT="http",
                A13N_ENVD_HTTP_BIND=origin.removeprefix("http://"),
                A13N_ENVD_HTTP_CREDENTIAL_FILE=str(credential),
                A13N_ENVD_HTTP_PLAINTEXT_SCOPE="loopback",
            )
        else:
            environment.update(
                A13N_ENVD_TRANSPORT="reverse_websocket",
                A13N_ENVD_REVERSE_WS_URL=origin,
                A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE=str(credential),
            )
        log = stack.enter_context((self.directory / "daemon.log").open("wb"))
        self.daemon_arguments = (str(self.binary()), "--config", str(config))
        self.daemon_environment, self.daemon_log, self.daemon_origin = environment, log, origin
        await self.launch_daemon()
        stack.push_async_callback(self.stop_daemon)

    async def launch_daemon(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.daemon_arguments, env=self.daemon_environment, stdout=self.daemon_log, stderr=self.daemon_log
        )
        if self.kind == "http-envd":
            async with asyncio.timeout(10):
                while True:
                    assert self.process.returncode is None, "File fixture daemon exited"
                    try:
                        _, writer = await asyncio.open_connection(
                            "127.0.0.1", int(self.daemon_origin.rsplit(":", 1)[1])
                        )
                    except OSError:
                        await anyio.sleep(0.02)
                        continue
                    writer.close()
                    await writer.wait_closed()
                    break

    async def stop_daemon(self):
        if self.process.returncode is None:
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), 5)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()

    async def outside_content(self):
        return (Path(self.outside) / "sentinel").read_text()

    def snapshot(self):
        return snapshot(self.root / "file-tests")
