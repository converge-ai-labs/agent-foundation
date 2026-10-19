"""Build an operator-owned Daytona snapshot for Envd execution and human desktop access.

Use a compatible Linux amd64 a13n-envd binary and DAYTONA_API_KEY with snapshot
build authority. The SDK is used only for uploading build inputs, not Agent execution.
No per-sandbox credentials or live daemons are baked into the snapshot.
"""

from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path


def main() -> None:
    from daytona import CreateSnapshotParams, Daytona, Image, Resources

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="New snapshot name")
    parser.add_argument("--region", default="us")
    parser.add_argument("--binary", required=True, type=Path, help="Compatible Linux amd64 a13n-envd binary")
    args = parser.parse_args()
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", args.name) is None:
        parser.error("Invalid snapshot name")
    repository = Path(__file__).resolve().parents[2]
    image = Image.base("debian:trixie-slim").run_commands(
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "python3 ca-certificates libgcc-s1 util-linux procps sudo xvfb openbox xauth x11-utils x11-xkb-utils "
        "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji chromium xterm mousepad xdotool "
        "&& groupadd --gid 1000 sandbox "
        "&& useradd --uid 1000 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*"
    )
    image = image.add_local_file(args.binary, "/usr/local/bin/a13n-envd")
    for source, name in (
        ("desktop.py", "a13n-sandbox-desktop"),
        ("entrypoint.sh", "a13n-sandbox-entrypoint"),
        ("init-skills.sh", "a13n-sandbox-init"),
    ):
        image = image.add_local_file(repository / "deploy/docker/images/sandbox" / source, "/usr/local/bin/" + name)
    image = (
        image.add_local_file(
            repository / "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
            "/opt/a13n-desktop-access.py",
        )
        .run_commands("chmod 0755 /usr/local/bin/a13n-* /opt/a13n-desktop-access.py")
        .workdir("/workspace")
    )
    # Daytona boots this entrypoint again after stop/start with the allocation's
    # environment variables. The shared launcher creates fresh desktop processes.
    startup = '#!/bin/sh\nset -eu\nif [ "$A13N_DESKTOP" = true ]; then exec a13n-sandbox-entrypoint a13n-sandbox-desktop; else exec a13n-sandbox-entrypoint a13n-envd; fi\n'
    image = image.run_commands(
        "printf %s "
        + base64.b64encode(startup.encode()).decode()
        + " | base64 -d > /usr/local/bin/a13n-daytona-start && chmod 0755 /usr/local/bin/a13n-daytona-start"
    )
    entrypoint = ["/usr/local/bin/a13n-daytona-start"]
    snapshot = Daytona().snapshot.create(
        CreateSnapshotParams(
            name=args.name,
            region_id=args.region,
            image=image,
            resources=Resources(cpu=2, memory=4, disk=10),
            entrypoint=entrypoint,
        ),
        timeout=900,
        on_logs=print,
    )
    print(f"Envd snapshot ready: {snapshot.name}")


if __name__ == "__main__":
    main()
