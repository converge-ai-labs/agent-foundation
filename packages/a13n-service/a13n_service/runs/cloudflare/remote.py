"""Cloudflare account-scoped operations and the bundled gateway compiler inputs."""

import asyncio
import json
import os
import re
import shutil
import signal
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

import httpx2

from a13n_service.settings import CloudflareProvisioning

REGISTRY = "registry.cloudflare.com"
DO_CLASS = "Environment"
MARKER = "A13N_DEPLOYMENT_ID"
IMAGE_MARKER = "dev.a13n.deployment"
MANIFEST_TYPES = ", ".join(
    (
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    )
)


class CloudFailure(Exception):
    """Only fixed, public failure codes leave this adapter."""

    def __init__(self, code: str, *, terminal: bool = False):
        super().__init__(code)
        self.code = code
        self.terminal = terminal


def checked_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9-]{32,36}", value):
        raise CloudFailure("cloudflare_invalid_response")
    try:
        UUID(value)
    except ValueError:
        raise CloudFailure("cloudflare_invalid_response") from None
    return value


async def command(
    binary: Path, *args: str, cwd: Path, env: dict[str, str], stdin: bytes | None = None, seconds: int, code: str
) -> None:
    if not binary.is_absolute():
        raise CloudFailure("deployment_tool_unavailable", terminal=True)
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
            start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise CloudFailure("deployment_tool_unavailable", terminal=True) from None
    try:
        async with asyncio.timeout(seconds):
            await process.communicate(stdin)
    except (TimeoutError, asyncio.CancelledError) as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        if isinstance(error, asyncio.CancelledError):
            raise
        raise CloudFailure(code) from None
    if process.returncode:
        raise CloudFailure(code)


def tool_environment(directory: Path) -> dict[str, str]:
    # Only the operator's resolved Node installation (no inherited secrets or npm config).
    node = shutil.which("node")
    return {
        "PATH": os.pathsep.join((str(Path(node).parent), os.defpath)) if node else os.defpath,
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "XDG_CONFIG_HOME": str(directory),
        "WRANGLER_SEND_METRICS": "false",
        "WRANGLER_LOG_PATH": str(directory / "wrangler.log"),
        "CI": "true",
    }


@dataclass(slots=True)
class Cloud:
    client: httpx2.AsyncClient
    settings: CloudflareProvisioning
    account: str
    worker: str
    deployment: str
    image: str
    api_token: str = field(repr=False)

    @property
    def script(self) -> str:
        return "/workers/scripts/" + self.worker

    @property
    def image_repo(self) -> str:
        return f"{self.account}/{self.worker}"

    async def request(self, method: str, path: str, *, optional: bool = False, **kwargs: Any) -> tuple[Any, dict]:
        try:
            response = await self.client.request(
                method,
                f"https://api.cloudflare.com/client/v4/accounts/{self.account}{path}",
                headers={"Authorization": "Bearer " + self.api_token},
                **kwargs,
            )
        except (httpx2.HTTPError, ValueError):
            raise CloudFailure("cloudflare_unavailable") from None
        if response.status_code in (401, 403):
            raise CloudFailure("cloudflare_authorization_required", terminal=True)
        try:
            body = response.json()
        except ValueError:
            raise CloudFailure("cloudflare_invalid_response") from None
        if not isinstance(body, dict):
            raise CloudFailure("cloudflare_invalid_response")
        if optional and response.status_code == 404:
            return None, {}
        if not response.is_success or body.get("success") is not True or "result" not in body:
            # Cloudflare can report authentication failure with HTTP 400.
            if any(
                isinstance(error, dict) and error.get("code") in {10000, 9109} for error in (body.get("errors") or [])
            ):
                raise CloudFailure("cloudflare_authorization_required", terminal=True)
            raise CloudFailure(
                "cloudflare_request_failed", terminal=400 <= response.status_code < 500 and response.status_code != 429
            )
        return body["result"], body.get("result_info") or {}

    async def endpoint(self) -> str:
        result, _ = await self.request("GET", "/workers/subdomain", optional=True)
        subdomain = result.get("subdomain") if isinstance(result, dict) else None
        if not isinstance(subdomain, str) or not re.fullmatch(r"[a-z0-9-]{1,63}", subdomain):
            raise CloudFailure("workers_subdomain_required", terminal=True)
        return f"https://{self.worker}.{subdomain}.workers.dev"

    async def worker_exists(self, *, owned: bool = True) -> bool:
        result, _ = await self.request("GET", self.script + "/settings", optional=True)
        if result is None:
            return False
        if not isinstance(result, dict) or not isinstance(result.get("bindings"), list):
            raise CloudFailure("cloudflare_invalid_response")
        if owned and not any(
            isinstance(binding, dict)
            and binding.get("name") == MARKER
            and binding.get("type") == "plain_text"
            and binding.get("text") == self.deployment
            for binding in result["bindings"]
        ):
            raise CloudFailure("worker_ownership_mismatch", terminal=True)
        return True

    async def namespace(self, expected: str | None = None) -> str | None:
        matches = []
        for page in range(1, 101):
            result, info = await self.request(
                "GET", "/workers/durable_objects/namespaces", params={"page": page, "per_page": 100}
            )
            # Cloudflare returns null for an empty namespace page with count zero.
            if result is None and info.get("count") == 0:
                result = []
            if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
                raise CloudFailure("cloudflare_invalid_response")
            matches.extend(
                checked_id(item.get("id"))
                for item in result
                if (item.get("script") == self.worker and item.get("class") == DO_CLASS)
                or (expected is not None and item.get("id") == expected)
            )
            if page >= info.get("total_pages", page + (len(result) == 100)):
                break
        else:
            raise CloudFailure("namespace_observation_incomplete")
        if len(matches) > 1 or (matches and expected is not None and matches[0] != expected):
            raise CloudFailure("namespace_ownership_mismatch", terminal=True)
        return matches[0] if matches else None

    async def application(self, namespace: str, expected: str | None = None) -> str | None:
        path = "/containers/applications"
        if expected:
            result, _ = await self.request("GET", f"{path}/{expected}", optional=True)
            if result is None:
                result = []
            else:
                result = [result]
        else:
            result, info = await self.request("GET", path)
            if info.get("total_pages", 1) > 1:
                raise CloudFailure("application_observation_incomplete")
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise CloudFailure("cloudflare_invalid_response")
        matches = [item for item in result if (item.get("durable_objects") or {}).get("namespace_id") == namespace]
        if len(matches) > 1 or (expected and result and not matches):
            raise CloudFailure("application_ownership_mismatch", terminal=True)
        if not matches:
            return None
        match = matches[0]
        if match.get("name") != self.worker or match.get("scheduling_policy") != "durable_object":
            raise CloudFailure("application_ownership_mismatch", terminal=True)
        app = checked_id(match.get("id"))
        if expected and app != expected:
            raise CloudFailure("application_ownership_mismatch", terminal=True)
        return app

    async def registry_password(self) -> str:
        result, _ = await self.request(
            "POST",
            f"/containers/registries/{REGISTRY}/credentials",
            json={"expiration_minutes": 5, "permissions": ["pull", "push"]},
        )
        if not isinstance(result, dict) or not isinstance(result.get("password"), str) or not result["password"]:
            raise CloudFailure("cloudflare_invalid_response")
        return result["password"]

    async def registry(self, method: str, password: str, expected: str | None = None) -> httpx2.Response:
        try:
            response = await self.client.request(
                method,
                f"https://{REGISTRY}/v2/{self.image_repo}/manifests/gateway",
                auth=("v1", password),
                headers={"Accept": MANIFEST_TYPES, **({"If-Match": f'"{expected}"'} if expected else {})},
            )
        except (httpx2.HTTPError, ValueError):
            raise CloudFailure("registry_unavailable") from None
        if not response.is_success and response.status_code != 404:
            raise CloudFailure("registry_request_failed")
        return response

    async def image_digest(self, password: str) -> str | None:
        response = await self.registry("HEAD", password)
        if response.status_code == 404:
            return None
        value = response.headers.get("Docker-Content-Digest", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise CloudFailure("registry_invalid_digest")
        return value

    async def owned_image(self, password: str, expected: str | None = None) -> str | None:
        response = await self.registry("GET", password)
        if response.status_code == 404:
            return None
        digest = response.headers.get("Docker-Content-Digest", "")
        try:
            manifest = response.json()
        except ValueError:
            raise CloudFailure("registry_invalid_manifest") from None
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get("annotations"), dict)
            or manifest["annotations"].get(IMAGE_MARKER) != self.deployment
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            or (expected is not None and digest != expected)
        ):
            raise CloudFailure("image_ownership_mismatch", terminal=True)
        return digest

    async def mirror(self) -> str:
        password = await self.registry_password()
        if digest := await self.owned_image(password):
            return digest
        with TemporaryDirectory(prefix="a13n-cloudflare-") as temp:
            directory = Path(temp)
            env = tool_environment(directory)
            await command(
                self.settings.regctl,
                "registry",
                "login",
                REGISTRY,
                "--user",
                "v1",
                "--pass-stdin",
                cwd=directory,
                env=env,
                stdin=password.encode(),
                seconds=10,
                code="registry_login_failed",
            )
            await command(
                self.settings.regctl,
                "image",
                "mod",
                self.image,
                "--create",
                f"{REGISTRY}/{self.image_repo}:gateway",
                "--annotation",
                f"{IMAGE_MARKER}={self.deployment}",
                cwd=directory,
                env=env,
                seconds=240,
                code="image_copy_failed",
            )
        if not (digest := await self.owned_image(password)):
            raise CloudFailure("image_digest_unconfirmed")
        return digest

    async def upload(self, gateway_token: str, service_origin: str | None, digest: str) -> None:
        await self.worker_exists()
        bundle = files(__package__)
        with TemporaryDirectory(prefix="a13n-cloudflare-") as temp:
            directory = Path(temp)
            (directory / "worker.mjs").write_bytes(bundle.joinpath("worker.mjs").read_bytes())
            config = json.loads(bundle.joinpath("wrangler.jsonc").read_bytes())
            config.update(name=self.worker, account_id=self.account, workers_dev=True, vars={MARKER: self.deployment})
            if service_origin:
                config["vars"]["SERVICE_ORIGIN"] = service_origin
            config["containers"][0]["name"] = self.worker
            config["containers"][0]["images"]["base"]["image"] = f"{REGISTRY}/{self.image_repo}@{digest}"
            (directory / "wrangler.json").write_text(json.dumps(config))
            fd = os.open(directory / "secrets.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as secrets:
                json.dump({"GATEWAY_TOKEN": gateway_token}, secrets)
            await command(
                self.settings.wrangler,
                "deploy",
                "--config",
                "wrangler.json",
                "--secrets-file",
                "secrets.json",
                cwd=directory,
                env={**tool_environment(directory), "CLOUDFLARE_API_TOKEN": self.api_token},
                seconds=340,
                code="worker_deploy_failed",
            )

    async def remove_namespace(self) -> None:
        if not await self.worker_exists():
            raise CloudFailure("namespace_requires_owned_worker", terminal=True)
        # Preserve the marker on the replacement so a lost receipt is safe to reconcile.
        metadata = {
            "main_module": "retired.mjs",
            "bindings": [{"type": "plain_text", "name": MARKER, "text": self.deployment}],
            "exports": {DO_CLASS: {"type": "durable-object", "state": "deleted"}},
        }
        await self.request(
            "PUT",
            self.script,
            files={
                "metadata": (None, json.dumps(metadata), "application/json"),
                "retired.mjs": (
                    "retired.mjs",
                    "export default {fetch: () => new Response(null, {status: 404})}",
                    "application/javascript+module",
                ),
            },
        )

    async def remove_image(self, expected: str | None) -> None:
        password = await self.registry_password()
        digest = await self.owned_image(password, expected)
        if digest is None:
            return
        # Cloudflare leaves tags present on digest DELETE. Only unlink the
        # deployment-owned tag; never delete shared layers or run registry GC.
        await self.registry("DELETE", password, expected=digest)
        if await self.image_digest(password) is not None:
            raise CloudFailure("image_removal_unconfirmed")
