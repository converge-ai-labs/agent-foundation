"""Build an operator-owned Modal image for Envd, Computer Use and human desktop access.

Authenticate with `modal token new` first. Supply a compatible Linux amd64
Envd binary. No running daemons or per-sandbox tokens are baked into the image.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    import modal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, help="Existing Modal App")
    parser.add_argument("--environment", default="main")
    parser.add_argument("--binary", required=True, type=Path, help="Compatible Linux amd64 a13n-envd binary")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    image = modal.Image.from_registry("debian:trixie-slim").run_commands(
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "python3 ca-certificates libgcc-s1 util-linux procps sudo xvfb openbox xauth x11-utils x11-xkb-utils "
        "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji chromium xterm mousepad xdotool "
        "&& groupadd --gid 1000 sandbox "
        "&& useradd --uid 1000 --gid sandbox --create-home --shell /bin/bash sandbox "
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*"
    )
    image = image.add_local_file(args.binary, "/usr/local/bin/a13n-envd", copy=True)
    for source, name in (
        ("desktop.py", "a13n-sandbox-desktop"),
        ("entrypoint.sh", "a13n-sandbox-entrypoint"),
        ("init-skills.sh", "a13n-sandbox-init"),
    ):
        image = image.add_local_file(
            repository / "deploy/docker/images/sandbox" / source, "/usr/local/bin/" + name, copy=True
        )
    image = (
        image.add_local_file(
            repository / "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
            "/opt/a13n-desktop-access.py",
            copy=True,
        )
        .run_commands("chmod 0755 /usr/local/bin/a13n-* /opt/a13n-desktop-access.py")
        .workdir("/workspace")
    )

    # The provider owns the launch command, including snapshot resumes.
    image = image.entrypoint([])
    app = modal.App.lookup(args.app, environment_name=args.environment)
    with modal.enable_output():
        image.build(app)
    print(f"Envd image ready: {image.object_id}")


if __name__ == "__main__":
    main()
