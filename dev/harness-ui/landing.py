"""Run the first-launch experience with disposable local state, without seeding."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from contextlib import chdir
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interface", choices=("cli", "webui"))
    options, arguments = parser.parse_known_args()
    if options.interface == "cli" and arguments:
        parser.error("CLI landing takes no options; use make cli for a configured session.")

    with tempfile.TemporaryDirectory(prefix="a13n-harness-ui-landing-") as temporary:
        root = Path(temporary).resolve()
        home = root / "home"
        workspace = root / "workspace"
        home.mkdir()
        # An explicit CODEX_HOME must already exist for the upstream policy resolver.
        (home / ".codex").mkdir()
        workspace.mkdir()
        # Isolate upstream account files as well as Harness UI configuration/history.
        # Keep ordinary provider keys and proxy settings available from the shell.
        environment = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "GROK_HOME": str(home / ".grok"),
            "GROK_AUTH_PATH": str(home / ".grok/auth.json"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "A13N_HARNESS_UI_DATA_ROOT": str(root / "data"),
        }
        previous = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)
        print(f"Disposable landing state: {root}\nExit the application (WebUI: Ctrl+C) to delete it.", file=sys.stderr)
        try:
            with chdir(workspace):
                from a13n_harness_ui.cli import cli

                # Leave the default YAML absent to exercise the real first-run path.
                cli.main(
                    args=[
                        "--no-update-check",
                        "--data-root",
                        str(root / "data"),
                        *(["webui", *arguments] if options.interface == "webui" else []),
                    ],
                    prog_name="a13n-harness-ui",
                )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    main()
