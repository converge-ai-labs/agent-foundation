"""Pinned E2B desktop build recipe and bounded control API calls (no business storage).

The cloud builder fetches only release- and checksum-verified public artifacts. Account credentials
remain on the control API and are never included in build steps or guest files.
"""

import hashlib
import json

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import NativeHTTP, decode_response
from pydantic import BaseModel, Field, JsonValue

from a13n_service.providers.environment.e2b_build import Build, BuildStatus, Template
from a13n_service.providers.environment.envd_release import installation_command

BASE_IMAGE = "debian:trixie-slim@sha256:4876fdea29cac1a33a3195f1eb1952feb1fca7dd6dd00be3e2f1879cf20d34d2"
SOURCE_REVISION = "1da9b5c08f1ed0a6095906583bef9555863eeb6a"
FILES = (
    (
        "deploy/docker/images/sandbox/entrypoint.sh",
        "/usr/local/bin/a13n-sandbox-entrypoint",
        "b3c53fb45406e3206ad6465bf4461b5fba385da67d1f11a3c534a83e066a0496",
    ),
    (
        "deploy/docker/images/sandbox/desktop.py",
        "/usr/local/bin/a13n-sandbox-desktop",
        "455fbe3413af57fe36fea0f4a88a1d5bfbfb6bbc6f044121d46f0ce210e785b9",
    ),
    (
        "deploy/docker/images/sandbox/init-skills.sh",
        "/usr/local/bin/a13n-sandbox-init",
        "e973546b911758e35c629d093d7b0496ce7e5af0342a7a10570f192a43508f47",
    ),
    (
        "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
        "/opt/a13n-desktop-access.py",
        "68f4ed23ac4729b57262738a3390e239c79db90a1010ae08bf41ed5a1e81ffb4",
    ),
)


def specification() -> dict[str, JsonValue]:
    install = (
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "curl python3 ca-certificates libgcc-s1 util-linux procps xvfb openbox xauth x11-utils x11-xkb-utils "
        "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji chromium xterm mousepad xdotool "
        "&& groupadd --gid 1001 sandbox && useradd --uid 1001 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*"
    )
    commands = [
        install,
        installation_command(),
    ]
    for source, destination, digest in FILES:
        url = f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{SOURCE_REVISION}/{source}"
        commands.append(
            f"curl -fL --retry 2 --max-time 60 '{url}' -o '{destination}' && echo '{digest}  {destination}' | sha256sum -c - && chmod 0755 '{destination}'"
        )
    commands.append(
        "test ! -e /var/lib/a13n-envd/http-token && test ! -e /run/a13n-desktop-access/control.sock && /usr/local/bin/a13n-envd --version"
    )
    steps: list[JsonValue] = [{"type": "RUN", "args": [cmd, "root"]} for cmd in commands]
    steps.extend([{"type": "USER", "args": ["root"]}, {"type": "WORKDIR", "args": ["/workspace"]}])
    return {
        "fromImage": BASE_IMAGE,
        "steps": steps,
        "startCmd": "sleep infinity",
        "readyCmd": "true",
    }


def revision() -> str:
    return hashlib.sha256(json.dumps(specification(), sort_keys=True).encode()).hexdigest()


class DesktopBuildAPI:
    def __init__(self, domain: str, token: str):
        self.http = NativeHTTP("e2b", f"https://api.{domain}", "", 25)
        self.http.client.headers.pop("Authorization", None)
        self.http.client.headers["X-API-Key"] = token

    async def close(self) -> None:
        await self.http.close()

    async def reserve(self, name: str) -> Build:
        raw = await self.http.request("POST", "/v3/templates", body={"name": name, "cpuCount": 2, "memoryMB": 2048})
        return decode_response(Build, raw, "e2b", mutation=True)

    async def find(self, name: str) -> Template | None:
        raw = await self.http.request("GET", f"/templates/aliases/{name}", missing=True)
        if raw is None:
            return None

        class Alias(BaseModel):
            template_id: str = Field(alias="templateID", pattern=r"^[a-zA-Z0-9_-]+$")

        alias = decode_response(Alias, raw, "e2b")
        return await self.inspect(alias.template_id, name)

    async def inspect(self, template_id: str, name: str) -> Template | None:
        raw = await self.http.request("GET", f"/templates/{template_id}", params={"limit": "2"}, missing=True)
        if raw is None:
            return None
        result = decode_response(Template, raw, "e2b")
        names = [n.split("/")[-1] for n in result.names + result.aliases]
        if result.template_id != template_id or result.public or name not in names or len(result.builds) != 1:
            raise failure("e2b", "desktop_template_identity_mismatch", Category.CONFLICT)
        return result

    async def submit(self, build: Build, spec: dict[str, JsonValue]) -> None:
        await self.http.request("POST", f"/v2/templates/{build.template_id}/builds/{build.build_id}", body=spec)

    async def status(self, build: Build) -> BuildStatus:
        raw = await self.http.request(
            "GET", f"/templates/{build.template_id}/builds/{build.build_id}/status", params={"limit": "1"}
        )
        status = decode_response(BuildStatus, raw, "e2b")
        if status.template_id != build.template_id or status.build_id != build.build_id:
            raise failure("e2b", "desktop_build_identity_mismatch", Category.CONFLICT)
        return status

    async def delete(self, template_id: str, name: str) -> None:
        if await self.inspect(template_id, name) is not None:
            await self.http.request("DELETE", f"/templates/{template_id}", missing=True)
