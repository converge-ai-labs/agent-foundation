"""Owned real backends for the Service/Harness Environment matrix."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import anyio

from .management_support import ManagementJourney
from .round_two_lab import free_origin, private_json

logger = logging.getLogger(__name__)
BACKENDS = ("local-envd", "docker", "e2b", "http-envd", "websocket-envd")
REMOTE = {"http-envd", "websocket-envd"}
RETENTION = {"idle": {"stop_after": None, "delete_after": None}}


def recipe_configuration(kind, root, settings=None):
    root.mkdir(mode=0o700)
    shell = [{"profile_id": "default", "executable": "/bin/sh", "fixed_arguments": ["-c"]}]
    if kind == "local-envd":
        return {"workspace": {"path": str(root)}, "shell_profiles": shell}
    if kind == "docker":
        root.chmod(0o777)
        return {
            "image": os.environ.get("LIVE_TEST_SANDBOX_IMAGE", "a13n-sandbox:local"),
            "pull_policy": "never",
            "mounts": [
                {"mount_id": "workspace", "container_path": "/workspace", "source": {"kind": "bind", "path": str(root)}}
            ],
            "shell_profiles": shell,
        }
    if kind == "e2b":
        return {"template": settings.template, "timeout_seconds": 300}
    return {}


@dataclass
class BackendTarget:
    backend: EnvironmentBackend
    provider: dict
    recipe: dict
    root: Path
    state: dict | None
    process: asyncio.subprocess.Process | None

    async def template(self, **overrides):
        return await self.backend.journey.post(
            self.backend.journey.base + "/environment-templates",
            {"name": "Backend matrix " + uuid4().hex, **self.recipe, **overrides},
        )

    async def allocate(self, *, access="full", preparation="on_run"):
        journey = self.backend.journey
        if self.backend.kind in REMOTE:
            body = {"provider_id": self.provider["id"], "configuration": {}, "state": self.state, "access": access}
        else:
            template = await self.template(access=access, preparation=preparation)
            body = {"template_id": template["id"]}
        return await journey.post(journey.base + "/environments", body)


class EnvironmentBackend:
    def __init__(self, lab, kind, binary=None, settings=None):
        self.lab, self.kind, self.binary, self.settings = lab, kind, binary, settings
        self.journey = ManagementJourney(lab)

    async def restart_worker(self):
        await self.lab.stop(self.lab.workers[-1])
        await self.lab.start_worker()

    @asynccontextmanager
    async def target(self):
        native_id = "env-" + uuid4().hex
        directory = self.lab.root / native_id
        directory.mkdir(mode=0o700)
        root = directory / "workspace"
        configuration = recipe_configuration(self.kind, root, self.settings)
        provider_body = {"name": "Backend matrix " + native_id, "type": "a13n." + self.kind, "configuration": {}}
        state, process = None, None
        try:
            if self.kind == "e2b":
                provider_body["credential"] = {"api_key": self.settings.api_key.get_secret_value()}
            elif self.kind in REMOTE:
                origin, token = free_origin(), secrets.token_urlsafe(32)
                if self.kind == "http-envd":
                    provider_body.update(configuration={"endpoint": origin}, credential={"token": token})
                else:
                    self.lab.config["reverse_envd"] = {"origin": origin, "token": token, "native_id": native_id}
                    private_json(self.lab.root / "config.json", self.lab.config)
                    await self.restart_worker()
                process = await self.start_daemon(directory, root, native_id, origin, token)
                state = {
                    "provider_key": "a13n." + self.kind,
                    "state_version": "1",
                    "state": {"daemon_environment_id": native_id},
                }
            provider = await self.journey.post(self.journey.base + "/environment-providers", provider_body)
            recipe = {
                "provider_id": provider["id"],
                "configuration": configuration,
                "access": "full",
                "preparation": "on_run",
                "retention": RETENTION,
            }
            logger.info("Environment matrix backend=%s provider=%s", self.kind, provider["id"])
            try:
                yield BackendTarget(self, provider, recipe, root, state, process)
            finally:
                with anyio.CancelScope(shield=True), anyio.fail_after(180):
                    await self.cleanup(provider["id"])
        finally:
            if process is not None and process.returncode is None:
                with anyio.CancelScope(shield=True):
                    await self.lab.stop(process)

    async def cleanup(self, provider_id):
        live = self.journey.live
        await live.cleanup()
        # Local Envd owns only per-Run processes; remote Envd is connect-only.
        # Their fixture process/temporary workspace cleanup belongs to the lab.
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
            # These daemons represent externally operated targets; local-envd
            # separately exercises its mandatory native isolation unchanged.
            "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
        }
        # This selects the test executable; it is not a daemon configuration field.
        environment.pop("A13N_ENVD_TEST_BINARY", None)
        if self.kind == "http-envd":
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
        if self.kind == "http-envd":
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
