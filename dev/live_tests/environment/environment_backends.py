"""Owned real backends for the Service/Harness Environment matrix."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import anyio

from ..infrastructure.management_support import ManagementJourney
from ..infrastructure.round_two_lab import free_origin, private_json
from ..infrastructure.tcp_proxy import TCPProxy

logger = logging.getLogger(__name__)
BACKENDS = ("docker", "e2b", "http_envd", "websocket_envd")
REMOTE = {"http_envd", "websocket_envd"}
RETENTION = {"idle": {"stop_after": None, "delete_after": None}}


def provider_configuration(kind, root, settings=None):
    root.mkdir(mode=0o700)
    shell = [{"profile_id": "default", "executable": "/bin/sh", "fixed_arguments": ["-c"]}]
    if kind == "direct_local":
        return {"root": {"path": str(root)}, "shell_profiles": shell}
    if kind == "docker":
        return {
            "image": os.environ.get("LIVE_TEST_DOCKER_IMAGE", "a13n-docker-environment:local"),
        }
    if kind == "e2b":
        return {"template": settings.template, "timeout_seconds": 300}
    return {}


@dataclass
class BackendTarget:
    backend: EnvironmentBackend
    provider: dict
    template_config: dict
    root: Path
    state: dict | None
    process: asyncio.subprocess.Process | None
    proxy: TCPProxy | None = None

    async def read_text(self, path):
        return await asyncio.to_thread((self.root / path).read_text)

    async def restart_daemon(self):
        await self.backend.lab.stop(self.process)
        self.process = await self.backend.launch_daemon(self.root.parent)

    async def template(self, **overrides):
        return await self.backend.journey.post(
            self.backend.journey.base + "/environment-templates",
            {"name": "Backend matrix " + uuid4().hex, **self.template_config, **overrides},
        )

    async def allocate(self, *, preparation="on_run"):
        journey = self.backend.journey
        if self.backend.kind in REMOTE:
            body = {"provider_id": self.provider["id"], "configuration": {}, "state": self.state}
        else:
            template = await self.template(preparation=preparation)
            body = {"template_id": template["id"]}
        resource = await journey.post(journey.base + "/environments", body)
        if self.backend.kind == "direct_local":
            self.root = Path(self.template_config["configuration"]["root"]["path"]) / "environments" / resource["id"]
            self.root.mkdir(parents=True, exist_ok=True)
        return resource


class EnvironmentBackend:
    def __init__(self, lab, kind, binary=None, settings=None, *, network_faults=False):
        self.lab, self.kind, self.binary, self.settings = lab, kind, binary, settings
        self.journey = ManagementJourney(lab)
        self.network_faults = network_faults
        self.daemon_launches = {}

    async def restart_worker(self):
        await self.lab.stop(self.lab.workers[-1])
        await self.lab.start_worker()

    @asynccontextmanager
    async def target(self):
        native_id = "env-" + uuid4().hex
        directory = self.lab.root / native_id
        directory.mkdir(mode=0o700)
        root = directory / "workspace"
        configuration = provider_configuration(self.kind, root, self.settings)
        provider_body = {"name": "Backend matrix " + native_id, "type": self.kind, "configuration": {}}
        state, process, proxy, target = None, None, None, None
        stack = AsyncExitStack()
        try:
            if self.kind == "e2b":
                provider_body["credential"] = {"api_key": self.settings.api_key.get_secret_value()}
            elif self.kind in REMOTE:
                origin, token = free_origin(), secrets.token_urlsafe(32)
                daemon_origin = origin
                if self.network_faults:
                    proxy = await stack.enter_async_context(
                        TCPProxy("127.0.0.1", int(origin.rsplit(":", 1)[1])).listen()
                    )
                if self.kind == "http_envd":
                    endpoint = f"http://127.0.0.1:{proxy.local_port}" if proxy else origin
                    provider_body.update(
                        configuration={"endpoint": endpoint, **({"request_timeout": 5} if proxy else {})},
                        credential={"token": token},
                    )
                else:
                    self.lab.config["reverse_envd"] = {"origin": origin, "token": token, "native_id": native_id}
                    private_json(self.lab.root / "config.json", self.lab.config)
                    await self.restart_worker()
                    if proxy:
                        daemon_origin = f"http://127.0.0.1:{proxy.local_port}"
                process = await self.start_daemon(directory, root, native_id, daemon_origin, token)
                state = {
                    "provider_key": self.kind,
                    "state_version": "1",
                    "state": {"daemon_environment_id": native_id},
                }
            provider = await self.journey.post(self.journey.base + "/environment-providers", provider_body)
            template_config = {
                "provider_id": provider["id"],
                "configuration": configuration,
                "preparation": "on_run",
                "retention": RETENTION,
            }
            logger.info("Environment matrix backend=%s provider=%s", self.kind, provider["id"])
            try:
                target = BackendTarget(self, provider, template_config, root, state, process, proxy)
                yield target
            finally:
                with anyio.CancelScope(shield=True), anyio.fail_after(180):
                    await self.cleanup(provider["id"])
        finally:
            if target is not None:
                process = target.process
            await stack.aclose()
            if process is not None and process.returncode is None:
                with anyio.CancelScope(shield=True):
                    await self.lab.stop(process)

    async def cleanup(self, provider_id):
        live = self.journey.live
        await live.cleanup()
        # Remote Envd is connect-only; the lab owns its process and directory.
        if self.kind not in {"docker", "e2b"}:
            return
        errors = []
        for environment in await live.collection(self.journey.base + "/environments"):
            if environment["provider_id"] != provider_id or environment["status"] == "deleted":
                continue
            try:
                await self.journey.environment_command(environment["id"], "delete")
                logger.info("Environment matrix deleted backend=%s environment=%s", self.kind, environment["id"])
            except Exception as error:
                errors.append(f"{environment['id']}: {type(error).__name__}")
        assert not errors, "Environment matrix cleanup failed: " + "; ".join(errors)

    async def start_daemon(self, directory, root, native_id, origin, token):
        runtime = directory / "runtime"
        runtime.mkdir(mode=0o700)
        token_path = directory / "token"
        with os.fdopen(os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            output.write(token)
        configuration = directory / "envd.json"
        private_json(
            configuration,
            {
                "root_mount_id": "workspace",
                "mounts": [
                    {
                        "mount_id": "workspace",
                        "native_root": str(root),
                        "writable": True,
                        "allow_command_execution": True,
                        "max_file_bytes": 1024 * 1024,
                        "allowed_operations": [
                            "stat",
                            "read_text",
                            "write_text",
                            "open_reader",
                            "open_writer",
                            "list",
                            "find",
                            "search",
                            "command_cwd",
                        ],
                    }
                ],
                "shell_profiles": [
                    {
                        "profile_id": "default",
                        "display_name": "Default",
                        "native_executable": "/bin/sh",
                        "fixed_arguments": ["-c"],
                        "safe_base_environment": {},
                        "executable_search_roots": ["/bin", "/usr/bin"],
                        "max_script_bytes": 1024 * 1024,
                        "allow_login_mode": False,
                    }
                ],
            },
        )
        environment = {
            **self.lab.environment,
            "A13N_ENVD_ENVIRONMENT_ID": native_id,
            "A13N_ENVD_RUNTIME_DIR": str(runtime),
            # These daemons represent externally operated targets; local_envd
            # separately exercises its mandatory native isolation unchanged.
            "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
        }
        # This selects the test executable; it is not a daemon configuration field.
        environment.pop("A13N_ENVD_TEST_BINARY", None)
        if self.kind == "http_envd":
            environment.update(
                A13N_ENVD_TRANSPORT="http",
                A13N_ENVD_HTTP_BIND=origin.removeprefix("http://"),
                A13N_ENVD_HTTP_CREDENTIAL_FILE=str(token_path),
                A13N_ENVD_HTTP_PLAINTEXT_SCOPE="loopback",
            )
        else:
            environment.update(
                A13N_ENVD_TRANSPORT="reverse_websocket",
                A13N_ENVD_REVERSE_WS_URL=origin.replace("http:", "ws:") + "/envd",
                A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE=str(token_path),
            )
        self.daemon_launches[directory] = (configuration, environment, origin)
        return await self.launch_daemon(directory)

    async def launch_daemon(self, directory):
        configuration, environment, origin = self.daemon_launches[directory]
        with (directory / "daemon.log").open("ab") as output:
            process = await asyncio.create_subprocess_exec(
                str(self.binary),
                "--config",
                str(configuration),
                env=environment,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        self.lab.processes.append(process)
        if self.kind == "http_envd":
            async with asyncio.timeout(10):
                while True:
                    assert process.returncode is None, f"Envd exited; inspect {directory / 'daemon.log'}"
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", int(origin.rsplit(":", 1)[1]))
                    except OSError:
                        await anyio.sleep(0.05)
                        continue
                    writer.close()
                    await writer.wait_closed()
                    break
        return process
