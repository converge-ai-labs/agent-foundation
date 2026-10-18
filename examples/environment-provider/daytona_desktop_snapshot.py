"""Build a Daytona snapshot with the Service desktop access supervisor and CJK fonts.

Set DAYTONA_API_KEY in the invoking process. Builds a new snapshot; never replaces
an existing name. Ordinary Computer operations still use Daytona's native API.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
from pathlib import Path
from urllib.parse import quote

from a13n_harness.providers.environment.native.http import NativeHTTP, decode_response
from a13n_service.providers.environment import desktop_guest
from pydantic import BaseModel


class Snapshot(BaseModel):
    id: str
    name: str
    state: str


def dockerfile() -> str:
    source = base64.b64encode(Path(desktop_guest.__file__).read_bytes()).decode()
    return "\n".join(
        [
            "FROM daytonaio/sandbox:0.9.0",
            "USER root",
            "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            "x11vnc websockify libxtst6 fonts-noto-cjk && fc-cache -f && rm -rf /var/lib/apt/lists/*",
            f"RUN printf '%s' '{source}' | base64 -d > /opt/a13n-desktop-access.py "
            "&& chmod 0755 /opt/a13n-desktop-access.py",
            "USER daytona",
            'ENTRYPOINT ["sudo", "-n", "python3", "-I", "/opt/a13n-desktop-access.py", "serve", '
            '"--root", "/run/a13n-desktop-access", "--display", ":0"]',
        ]
    )


async def build(name: str, region: str) -> None:
    transport = NativeHTTP("daytona", "https://app.daytona.io/api/", os.environ["DAYTONA_API_KEY"], 60)
    try:
        snapshot = decode_response(
            Snapshot,
            await transport.request(
                "POST",
                "snapshots",
                body={
                    "name": name,
                    "regionId": region,
                    "cpu": 2,
                    "memory": 4,
                    "disk": 10,
                    "buildInfo": {"dockerfileContent": dockerfile(), "contextHashes": []},
                },
            ),
            "daytona",
            mutation=True,
        )
        print(f"Snapshot build accepted: {snapshot.id}", flush=True)
        # A timeout leaves the build intact for reconciliation by ID, never retries creation.
        async with asyncio.timeout(900):
            previous = None
            while snapshot.state not in {"active", "error", "build_failed"}:
                if snapshot.state != previous:
                    print(f"Snapshot state: {snapshot.state}", flush=True)
                    previous = snapshot.state
                await asyncio.sleep(5)
                snapshot = decode_response(
                    Snapshot, await transport.request("GET", "snapshots/" + quote(snapshot.id, safe="")), "daytona"
                )
        if snapshot.state != "active":
            raise RuntimeError(f"Snapshot {snapshot.id} did not become active; inspect its build logs")
        print(f"Desktop access snapshot ready: {snapshot.name}", flush=True)
    finally:
        await transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="New snapshot name")
    parser.add_argument("--region", default="us")
    args = parser.parse_args()
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", args.name) is None:
        parser.error("Invalid snapshot name")
    asyncio.run(build(args.name, args.region))


if __name__ == "__main__":
    main()
