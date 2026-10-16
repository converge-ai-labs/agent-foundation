"""Build an opt-in E2B template for Agent-requested desktop assistance.

This adds the endpoint required by Service desktop_assistance=true.
The official Desktop SDK continues to initialize and operate the desktop.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_service.providers.environment import desktop_guest
from e2b import Template, default_build_logger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="New E2B template name (an existing name is updated)")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="a13n-desktop-template-") as directory:
        source = Path(directory) / "desktop_access.py"
        source.write_text(Path(desktop_guest.__file__).read_text())
        template = (
            Template(file_context_path=directory)
            .from_template("desktop")
            .run_cmd(
                "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                "x11vnc websockify libxtst6 fonts-noto-cjk && fc-cache -f && rm -rf /var/lib/apt/lists/*",
                user="root",
            )
            .copy("desktop_access.py", "/opt/a13n-desktop-access.py", user="root", mode=0o755)
            .set_user("user")
            .set_start_cmd(
                "sudo -n python3 -I /opt/a13n-desktop-access.py serve --root /run/a13n-desktop-access --display :0",
                "sudo -n test -S /run/a13n-desktop-access/control.sock",
            )
        )
        result = Template.build(template, args.name, cpu_count=2, memory_mb=2048, on_build_logs=default_build_logger())
    print(f"Desktop access template ready: {result.template_id}")


if __name__ == "__main__":
    main()
