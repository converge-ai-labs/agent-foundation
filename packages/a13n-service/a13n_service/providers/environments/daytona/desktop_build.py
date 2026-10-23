"""Pinned public Dockerfile built asynchronously by Daytona, without local uploads."""

import base64
import hashlib
import json
from urllib.parse import quote

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.daytona.shared import DaytonaConnectionConfiguration, organization
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import NativeHTTP, decode_response
from pydantic import BaseModel, Field, JsonValue

from a13n_service.providers.environment.daytona_build import Snapshot

BASE_IMAGE = "debian:trixie-slim@sha256:4876fdea29cac1a33a3195f1eb1952feb1fca7dd6dd00be3e2f1879cf20d34d2"
SOURCE_REVISION = "25bab435d27e3d30fc038bfa8d2f20e5d70cf783"
RELEASE_URL = "https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v0.5.1/a13n-envd-0.5.1-x86_64-unknown-linux-gnu.tar.gz"
RELEASE_SHA256 = "f2fd6eace212c2e59d2b4d6a2dc69ec010235005ad332ac1ddd1932f7bca258c"
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
        "7d31432661c8199a95bed5214088a2d3589872f787ad55b28566ba9b659b9571",
    ),
)


def _assets() -> dict[str, JsonValue]:
    install = (
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "curl python3 ca-certificates libgcc-s1 util-linux procps sudo xvfb openbox xauth x11-utils x11-xkb-utils "
        "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji chromium xterm mousepad xdotool "
    )
    configure = (
        "groupadd --gid 1000 sandbox && useradd --uid 1000 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*"
    )
    # Keep apt installation and cleanup in one layer.
    commands: list[JsonValue] = [
        " && ".join(("apt-get update", install, configure)),
        f"curl -fL --retry 2 --max-time 120 '{RELEASE_URL}' -o /tmp/envd.tar.gz && echo '{RELEASE_SHA256}  /tmp/envd.tar.gz' | sha256sum -c - && tar -xOzf /tmp/envd.tar.gz a13n-envd > /usr/local/bin/a13n-envd && chmod 0755 /usr/local/bin/a13n-envd && rm /tmp/envd.tar.gz",
    ]
    for source, destination, digest in FILES:
        url = f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{SOURCE_REVISION}/{source}"
        commands.append(
            f"curl -fL --retry 2 --max-time 60 '{url}' -o '{destination}' && echo '{digest}  {destination}' | sha256sum -c - && chmod 0755 '{destination}'"
        )
    commands.append(
        "test ! -e /var/lib/a13n-envd/http-token && test ! -e /run/a13n-desktop-access/control.sock && /usr/local/bin/a13n-envd --version"
    )
    return {"base": BASE_IMAGE, "commands": commands}


def specification() -> dict[str, JsonValue]:
    assets = _assets()
    commands = assets["commands"]
    assert isinstance(commands, list)
    startup = '#!/bin/sh\nset -eu\nif [ "$A13N_DESKTOP" = true ]; then exec a13n-sandbox-entrypoint a13n-sandbox-desktop; else exec a13n-sandbox-entrypoint a13n-envd; fi\n'
    encoded = base64.b64encode(startup.encode()).decode()
    dockerfile = "FROM " + str(assets["base"]) + "\n" + "\n".join("RUN " + str(c) for c in commands)
    dockerfile += f"\nRUN printf %s {encoded} | base64 -d > /usr/local/bin/a13n-daytona-start && chmod 0755 /usr/local/bin/a13n-daytona-start\n"
    dockerfile += 'WORKDIR /workspace\nENTRYPOINT ["/usr/local/bin/a13n-daytona-start"]\n'
    return {"dockerfile": dockerfile, "cpu": 2, "memory": 4, "disk": 10}


def revision() -> str:
    return hashlib.sha256(json.dumps(specification(), sort_keys=True).encode()).hexdigest()


class BuildInfo(BaseModel):
    dockerfileContent: str


class NativeSnapshot(BaseModel):
    id: str = Field(min_length=1)
    name: str
    organizationId: str
    general: bool
    state: str
    buildInfo: BuildInfo


class DesktopBuildAPI:
    def __init__(self, configuration: DaytonaConnectionConfiguration, credential: TokenCredential):
        self.configuration = configuration
        self.http = NativeHTTP("daytona", "https://app.daytona.io/api/", credential.api_key.get_secret_value(), 25)
        self.account: str | None = None

    async def _account(self) -> str:
        if self.account is None:
            self.account = await organization(self.http, self.configuration.organization_id)
        return self.account

    async def _validate(
        self, raw: JsonValue, name: str, manifest: dict[str, JsonValue], identity: str | None = None
    ) -> Snapshot:
        snapshot = decode_response(NativeSnapshot, raw, "daytona")
        if (
            snapshot.general
            or snapshot.organizationId != await self._account()
            or snapshot.name != name
            or snapshot.buildInfo.dockerfileContent != manifest["dockerfile"]
            or (identity is not None and snapshot.id != identity)
        ):
            raise failure("daytona", "desktop_snapshot_identity_mismatch", Category.CONFLICT)
        return Snapshot(id=snapshot.id, state=snapshot.state)

    async def find(self, name: str, manifest: dict[str, JsonValue], identity: str | None = None) -> Snapshot | None:
        await self._account()
        raw = await self.http.request("GET", "snapshots/" + quote(identity or name, safe=""), missing=True)
        return None if raw is None else await self._validate(raw, name, manifest, identity)

    async def create(self, name: str, manifest: dict[str, JsonValue]) -> Snapshot:
        await self._account()
        raw = await self.http.request(
            "POST",
            "snapshots",
            body={
                "name": name,
                "regionId": self.configuration.target,
                "cpu": manifest["cpu"],
                "memory": manifest["memory"],
                "disk": manifest["disk"],
                "buildInfo": {"dockerfileContent": manifest["dockerfile"], "contextHashes": []},
            },
        )
        try:
            return await self._validate(raw, name, manifest)
        except EnvironmentProviderError:
            raise failure(
                "daytona",
                "desktop_snapshot_submission_unresolved",
                Category.UNKNOWN_OUTCOME,
                certainty=Certainty.UNKNOWN,
            ) from None

    async def delete(self, name: str, manifest: dict[str, JsonValue], identity: str) -> None:
        if await self.find(name, manifest, identity) is None:
            return
        await self.http.request("DELETE", "snapshots/" + quote(identity, safe=""), missing=True)
        if await self.find(name, manifest, identity) is not None:
            raise failure("daytona", "desktop_snapshot_cleanup_pending", Category.UNAVAILABLE)

    async def close(self) -> None:
        await self.http.close()
