"""Build a Vercel snapshot with Envd and desktop prerequisites, without live credentials.

Run from the repository with VERCEL_TOKEN, VERCEL_TEAM_ID and VERCEL_PROJECT_ID
in the environment. The temporary build sandbox is deleted; the resulting snapshot
is retained. This allocates a billable sandbox and installs official Google Chrome.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import io
import os
import re
import secrets
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.http import HttpReference
from a13n_harness.providers.environment.vercel.shared import (
    SandboxResponse,
    VercelConnectionConfiguration,
    VercelEnvironmentConfiguration,
)
from pydantic import BaseModel, SecretStr


class Snapshot(BaseModel):
    id: str


class SnapshotResponse(BaseModel):
    snapshot: Snapshot


async def build(binary: Path | None, archive_url: str | None, archive_sha256: str | None) -> None:
    repository = Path(__file__).resolve().parents[2]
    backend = VercelConnectionConfiguration(
        team_id=os.environ["VERCEL_TEAM_ID"], project_id=os.environ["VERCEL_PROJECT_ID"]
    )
    runtime = NativeRuntime(backend, TokenCredential(api_key=SecretStr(os.environ["VERCEL_TOKEN"])))
    reference = HttpReference(
        VercelEnvironmentConfiguration(request_timeout_seconds=600), "snapshot-builder", None, runtime
    )
    name = "a13n-envd-build-" + secrets.token_hex(8)
    http = reference.transport()
    try:
        raw = await http.request(
            "POST",
            "/v3/sandboxes",
            body={
                "name": name,
                "projectId": backend.project_id,
                "image": "vercel/sandbox/ubuntu",
                "persistent": False,
                "timeout": 1800000,
                "resources": {"vcpus": 2},
            },
        )
        session_id = SandboxResponse.model_validate(raw).session.id
        print("Installing desktop prerequisites", flush=True)
        await command(
            reference,
            session_id,
            [
                "sh",
                "-c",
                "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                "python3 ca-certificates curl libgcc-s1 util-linux procps sudo xvfb openbox xauth x11-utils x11-xkb-utils "
                "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji xterm mousepad xdotool "
                "&& curl -fsSLo /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb "
                "&& apt-get install -y /tmp/google-chrome.deb && rm /tmp/google-chrome.deb "
                "&& ln -s /usr/bin/google-chrome-stable /usr/local/bin/chromium "
                "&& groupadd --gid 1001 sandbox && useradd --uid 1001 --gid sandbox --create-home --shell /bin/bash sandbox "
                "&& install -d -o sandbox -g sandbox /workspace "
                "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
                "&& rm -rf /var/lib/apt/lists/*",
            ],
            sudo=True,
            timeout=600,
        )
        # Bounded archives avoid large request bodies on provider/proxy paths.
        # Reassemble only after every part arrived, and verify the exact local binary.
        binary_data = binary.read_bytes() if binary is not None else b""
        compressed_binary = gzip.compress(binary_data) if binary_data else b""
        inputs: list[tuple[Path, str]] = []
        chunks = [
            (f"envd-part-{index:04d}", compressed_binary[offset : offset + 64 * 1024])
            for index, offset in enumerate(range(0, len(compressed_binary), 64 * 1024))
        ]
        for source, name_ in (
            ("desktop.py", "a13n-sandbox-desktop"),
            ("entrypoint.sh", "a13n-sandbox-entrypoint"),
            ("init-skills.sh", "a13n-sandbox-init"),
        ):
            inputs.append((repository / "deploy/docker/images/sandbox" / source, name_))
        inputs.append(
            (
                repository / "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
                "a13n-desktop-access.py",
            )
        )
        chunks.extend((name_, source.read_bytes()) for source, name_ in inputs)
        await command(reference, session_id, ["mkdir", "-p", "/tmp/a13n-build"], timeout=15)
        print("Uploading verified daemon and launchers", flush=True)
        slots = asyncio.Semaphore(4)

        async def upload(name_: str, data: bytes) -> None:
            async with slots:
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode="w:gz", format=tarfile.USTAR_FORMAT) as archive:
                    entry = tarfile.TarInfo("tmp/a13n-build/" + name_)
                    entry.size = len(data)
                    entry.mode = 0o600
                    archive.addfile(entry, io.BytesIO(data))
                response = await http.client.post(
                    f"/v2/sandboxes/sessions/{session_id}/fs/write",
                    content=payload.getvalue(),
                    headers={"Content-Type": "application/gzip", "x-cwd": "/"},
                    timeout=httpx2.Timeout(60),
                )
                http.check(response, mutation=True)

        async with asyncio.TaskGroup() as group:
            for name_, data in chunks:
                group.create_task(upload(name_, data))
        if archive_url is not None:
            assert archive_sha256 is not None
            await command(
                reference,
                session_id,
                [
                    "sh",
                    "-c",
                    'curl --fail --location --max-time 120 "$1" -o /tmp/a13n-build/envd.tar.gz '
                    '&& printf "%s  /tmp/a13n-build/envd.tar.gz\\n" "$2" | sha256sum -c - '
                    "&& tar -xOzf /tmp/a13n-build/envd.tar.gz a13n-envd > /tmp/a13n-build/a13n-envd",
                    "download-envd",
                    archive_url,
                    archive_sha256,
                ],
                timeout=150,
            )
        else:
            await command(
                reference,
                session_id,
                [
                    "sh",
                    "-c",
                    "cat /tmp/a13n-build/envd-part-* | gzip -d > /tmp/a13n-build/a13n-envd "
                    f"&& echo '{hashlib.sha256(binary_data).hexdigest()}  /tmp/a13n-build/a13n-envd' | sha256sum -c -",
                ],
                timeout=30,
            )
        await command(
            reference,
            session_id,
            [
                "sh",
                "-c",
                "install -m 0755 /tmp/a13n-build/a13n-envd /tmp/a13n-build/a13n-sandbox-* /usr/local/bin/ "
                "&& install -m 0755 /tmp/a13n-build/a13n-desktop-access.py /opt/a13n-desktop-access.py "
                "&& rm -rf /tmp/a13n-build && test ! -e /var/lib/a13n-envd/http-token",
            ],
            sudo=True,
            timeout=30,
        )
        print("Creating snapshot", flush=True)
        snapshot = await http.request("POST", f"/v2/sandboxes/sessions/{session_id}/snapshot", body={"expiration": 0})
        print("Envd snapshot ready:", SnapshotResponse.model_validate(snapshot).snapshot.id, flush=True)
    finally:
        # The unique name also permits cleanup after a lost allocation response.
        try:
            await http.request(
                "DELETE",
                "/v2/sandboxes/" + name,
                params={"projectId": backend.project_id, "deleteOrphanSnapshots": "false"},
                missing=True,
            )
        finally:
            await reference.close_transport()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--binary", type=Path)
    source.add_argument("--archive-url", help="HTTPS release archive containing a13n-envd")
    parser.add_argument("--archive-sha256", help="Verified SHA256SUMS entry for the release archive")
    args = parser.parse_args()
    if args.binary is not None and not args.binary.is_file():
        parser.error("A compatible Linux amd64 Envd binary is required")
    if args.archive_url is not None:
        url = urlsplit(args.archive_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            parser.error("An HTTPS release archive URL without embedded credentials is required")
        if re.fullmatch(r"[a-fA-F0-9]{64}", args.archive_sha256 or "") is None:
            parser.error("The release archive requires its verified SHA-256 checksum")
    elif args.archive_sha256 is not None:
        parser.error("--archive-sha256 requires --archive-url")
    asyncio.run(build(args.binary, args.archive_url, args.archive_sha256))


if __name__ == "__main__":
    main()
