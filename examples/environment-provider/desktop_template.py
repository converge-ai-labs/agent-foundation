"""Build an operator-owned E2B desktop template with Chinese/Japanese/Korean fonts."""

from __future__ import annotations

import argparse

from e2b import Template, default_build_logger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="New E2B template name (an existing name is updated)")
    args = parser.parse_args()
    template = (
        Template()
        .from_template("desktop")
        .run_cmd(
            "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            'fonts-noto-cjk && fc-cache -f && test -n "$(fc-list :lang=zh family)" '
            "&& rm -rf /var/lib/apt/lists/*",
            user="root",
        )
        .set_user("user")
    )
    result = Template.build(template, args.name, cpu_count=2, memory_mb=2048, on_build_logs=default_build_logger())
    print(f"Desktop template ready: {result.template_id}")


if __name__ == "__main__":
    main()
