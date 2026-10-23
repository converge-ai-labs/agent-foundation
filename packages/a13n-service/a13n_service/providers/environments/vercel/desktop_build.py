"""Bounded Vercel build operations; no credentials or running services in snapshots."""

import base64
import hashlib
import json
from urllib.parse import quote

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.http import HttpReference
from a13n_harness.providers.environment.vercel.shared import (
    SandboxResponse,
    VercelConnectionConfiguration,
    VercelEnvironmentConfiguration,
)
from pydantic import BaseModel, Field, JsonValue

from a13n_service.providers.environment.vercel_build import BuildTarget, Snapshot

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


def specification() -> dict[str, JsonValue]:
    commands = [
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "python3 ca-certificates curl libgcc-s1 util-linux procps sudo xvfb openbox xauth x11-utils x11-xkb-utils "
        "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji xterm mousepad xdotool "
        "&& curl -fL --retry 2 --max-time 120 -o /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb "
        "&& apt-get install -y /tmp/google-chrome.deb && rm /tmp/google-chrome.deb "
        "&& ln -s /usr/bin/google-chrome-stable /usr/local/bin/chromium "
        "&& groupadd --gid 1001 sandbox && useradd --uid 1001 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*",
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
    return {"image": "vercel/sandbox/ubuntu", "script": "set -eu\n" + "\n".join(commands)}


def revision() -> str:
    return _digest(specification())


def _digest(manifest: dict[str, JsonValue]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


class NativeSnapshot(BaseModel):
    id: str = Field(pattern=r"^snap_[A-Za-z0-9_-]+$")
    sourceSessionId: str
    status: str
    creationMethod: str
    expiresAt: int | None = None


class SnapshotResponse(BaseModel):
    snapshot: NativeSnapshot


class Pagination(BaseModel):
    next: str | None = None


class SnapshotPage(BaseModel):
    snapshots: list[NativeSnapshot]
    pagination: Pagination


class DesktopBuildAPI:
    def __init__(self, configuration: VercelConnectionConfiguration, credential: TokenCredential):
        self.configuration = configuration
        self.reference = HttpReference(
            VercelEnvironmentConfiguration(request_timeout_seconds=45),
            "desktop-builder",
            None,
            NativeRuntime(configuration, credential),
        )
        self.http = self.reference.transport()

    @staticmethod
    def _tags(name: str, manifest: dict[str, JsonValue]) -> dict[str, str]:
        return {"a13n-desktop-build": name, "a13n-manifest": _digest(manifest)}

    def _accept(
        self, raw: JsonValue, name: str, manifest: dict[str, JsonValue], target: BuildTarget | None
    ) -> BuildTarget:
        value = decode_response(SandboxResponse, raw, "vercel")
        observed = BuildTarget(session_id=value.session.id, created_at=value.sandbox.createdAt)
        if (
            value.sandbox.name != name
            or value.sandbox.persistent
            or any(value.sandbox.tags.get(k) != v for k, v in self._tags(name, manifest).items())
            or (target is not None and target != observed)
        ):
            raise failure("vercel", "desktop_builder_identity_mismatch", Category.CONFLICT)
        return observed

    async def find_builder(
        self, name: str, manifest: dict[str, JsonValue], target: BuildTarget | None
    ) -> BuildTarget | None:
        raw = await self.http.request(
            "GET",
            "/v2/sandboxes/" + quote(name, safe=""),
            params={"projectId": self.configuration.project_id, "resume": "false"},
            missing=True,
        )
        return None if raw is None else self._accept(raw, name, manifest, target)

    async def create(self, name: str, manifest: dict[str, JsonValue]) -> BuildTarget:
        raw = await self.http.request(
            "POST",
            "/v3/sandboxes",
            body={
                "name": name,
                "projectId": self.configuration.project_id,
                "image": manifest["image"],
                "persistent": False,
                "timeout": 1800000,
                "resources": {"vcpus": 2},
                "tags": self._tags(name, manifest),
            },
        )
        return self._accept(raw, name, manifest, None)

    async def install(self, name: str, manifest: dict[str, JsonValue], target: BuildTarget) -> str:
        if await self.find_builder(name, manifest, target) is None:
            raise failure("vercel", "desktop_builder_missing", Category.MISSING)
        # A root-private lock and terminal status make a lost launch response safe to rejoin.
        # A crashed installation is never silently rerun over a partially modified filesystem.
        script = """#!/bin/sh
set -eu
exec 9>/var/lib/a13n-build/lock
flock -n 9 || exit 0
[ ! -e /var/lib/a13n-build/status ] || exit 0
printf running > /var/lib/a13n-build/status
trap 'printf failed > /var/lib/a13n-build/status' EXIT
sh /var/lib/a13n-build/install.sh
printf ready > /var/lib/a13n-build/status
trap - EXIT
"""
        encoded = base64.b64encode(str(manifest["script"]).encode()).decode()
        launcher = base64.b64encode(script.encode()).decode()
        output = await command(
            self.reference,
            target.session_id,
            [
                "sh",
                "-c",
                "install -d -m 0700 /var/lib/a13n-build; "
                "if [ ! -e /var/lib/a13n-build/status ]; then "
                f"printf %s {encoded} | base64 -d > /var/lib/a13n-build/install.sh; "
                f"printf %s {launcher} | base64 -d > /var/lib/a13n-build/launch.sh; "
                "nohup sh /var/lib/a13n-build/launch.sh > /var/lib/a13n-build/output 2>&1 < /dev/null & fi; "
                "cat /var/lib/a13n-build/status 2>/dev/null || printf pending",
            ],
            sudo=True,
            timeout=10,
            max_output=1024,
        )
        status = output.strip()
        if status not in {"pending", "running", "ready", "failed"}:
            raise failure("vercel", "desktop_builder_status_invalid", Category.CONFLICT)
        return status

    def _snapshot(self, value: NativeSnapshot, target: BuildTarget, identity: str | None) -> Snapshot:
        if (
            value.sourceSessionId != target.session_id
            or value.creationMethod != "manual"
            or value.expiresAt is not None
            or (identity is not None and identity != value.id)
        ):
            raise failure("vercel", "desktop_snapshot_identity_mismatch", Category.CONFLICT)
        return Snapshot(id=value.id, state=value.status)

    async def snapshot(self, name: str, manifest: dict[str, JsonValue], target: BuildTarget) -> Snapshot:
        if await self.find_builder(name, manifest, target) is None:
            raise failure("vercel", "desktop_builder_missing", Category.MISSING)
        raw = await self.http.request(
            "POST", f"/v2/sandboxes/sessions/{quote(target.session_id, safe='')}/snapshot", body={"expiration": 0}
        )
        return self._snapshot(decode_response(SnapshotResponse, raw, "vercel", mutation=True).snapshot, target, None)

    async def find_snapshot(self, name: str, target: BuildTarget, identity: str | None) -> Snapshot | None:
        if identity is not None:
            raw = await self.http.request("GET", "/v2/sandboxes/snapshots/" + quote(identity, safe=""), missing=True)
            if raw is None:
                return None
            snapshot = self._snapshot(decode_response(SnapshotResponse, raw, "vercel").snapshot, target, identity)
            return None if snapshot.state == "deleted" else snapshot
        # Only this unique builder's snapshots are candidates; pagination never means absence.
        params = {"project": self.configuration.project_id, "name": name, "limit": "100"}
        found: list[Snapshot] = []
        for _ in range(20):
            raw = await self.http.request("GET", "/v2/sandboxes/snapshots", params=params)
            page = decode_response(SnapshotPage, raw, "vercel")
            found.extend(self._snapshot(item, target, None) for item in page.snapshots)
            if page.pagination.next is None:
                if len(found) > 1:
                    raise failure("vercel", "desktop_snapshot_identity_mismatch", Category.CONFLICT)
                return found[0] if found else None
            params["cursor"] = page.pagination.next
        raise failure("vercel", "desktop_snapshot_listing_incomplete", Category.UNAVAILABLE)

    async def delete_builder(self, name: str, manifest: dict[str, JsonValue], target: BuildTarget) -> None:
        if await self.find_builder(name, manifest, target) is None:
            return
        await self.http.request(
            "DELETE",
            "/v2/sandboxes/" + quote(name, safe=""),
            params={"projectId": self.configuration.project_id, "deleteOrphanSnapshots": "false"},
            missing=True,
        )
        if await self.find_builder(name, manifest, target) is not None:
            raise failure("vercel", "desktop_builder_cleanup_pending", Category.UNAVAILABLE)

    async def delete_snapshot(self, name: str, target: BuildTarget, identity: str) -> None:
        if await self.find_snapshot(name, target, identity) is None:
            return
        await self.http.request("DELETE", "/v2/sandboxes/snapshots/" + quote(identity, safe=""), missing=True)
        if await self.find_snapshot(name, target, identity) is not None:
            raise failure("vercel", "desktop_snapshot_cleanup_pending", Category.UNAVAILABLE)

    async def close(self) -> None:
        await self.http.close()
