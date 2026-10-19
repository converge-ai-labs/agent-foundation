"""Build an E2B template with a13n-envd, an X11 desktop and revocable viewer access.

Run from a source checkout with a compatible Linux amd64 a13n-envd binary.
Services and per-sandbox credentials are initialized by the lifecycle Provider,
never during template build or fixed-target attachment.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_service.providers.environment import desktop_guest
from e2b import Template, default_build_logger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="Operator-owned E2B template name")
    parser.add_argument("--binary", type=Path, required=True, help="Linux amd64 a13n-envd binary")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    with TemporaryDirectory(prefix="a13n-envd-template-") as directory:
        context = Path(directory)
        shutil.copyfile(args.binary, context / "a13n-envd")
        shutil.copyfile(desktop_guest.__file__, context / "desktop_access.py")
        for source, name in (
            ("desktop.py", "a13n-sandbox-desktop"),
            ("entrypoint.sh", "a13n-sandbox-entrypoint"),
            ("init-skills.sh", "a13n-sandbox-init"),
        ):
            shutil.copyfile(repository / "deploy/docker/images/sandbox" / source, context / name)
        template = (
            Template(file_context_path=directory)
            .from_image("debian:trixie-slim")
            .run_cmd(
                "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                "python3 ca-certificates libgcc-s1 util-linux procps xvfb openbox xauth x11-utils x11-xkb-utils "
                "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji chromium xterm mousepad xdotool "
                "&& groupadd --gid 1001 sandbox "
                "&& useradd --uid 1001 --gid sandbox --create-home --shell /bin/bash sandbox "
                "&& install -d -o sandbox -g sandbox /workspace "
                "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
                "&& rm -rf /var/lib/apt/lists/*",
                user="root",
            )
        )
        for name in ("a13n-envd", "a13n-sandbox-desktop", "a13n-sandbox-entrypoint", "a13n-sandbox-init"):
            template = template.copy(name, "/usr/local/bin/" + name, user="root", mode=0o755)
        template = (
            template.copy("desktop_access.py", "/opt/a13n-desktop-access.py", user="root", mode=0o755)
            .set_user("root")
            .set_workdir("/workspace")
            .set_start_cmd("sleep infinity", "true")
        )
        result = Template.build(template, args.name, cpu_count=2, memory_mb=2048, on_build_logs=default_build_logger())
    print(f"Envd desktop template ready: {result.template_id}")


if __name__ == "__main__":
    main()
