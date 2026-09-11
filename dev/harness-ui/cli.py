"""Seed an isolated development configuration, then run the ordinary Harness UI CLI."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import click
from a13n_harness_ui.cli import cli
from a13n_harness_ui.settings_loader import default_harness_ui_settings_path, resolve_harness_ui_data_root
from filelock import FileLock, Timeout

ROOT = Path(__file__).resolve().parents[2]
RESOURCE_SUFFIXES = {
    "models": (".yaml",),
    "agents": (".yaml",),
    "extensions": (".yaml",),
    "mcp": (".yaml", ".json"),
    "projects": (".yaml",),
    "subagents": (".md",),
}


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _write_missing(target: Path, content: bytes) -> None:
    """Publish a complete private file without replacing an existing destination."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.is_symlink():
        raise OSError(f"Development configuration destination must not be a symlink: {target.parent}")
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".initialize-") as temporary:
        temporary.write(content)
        temporary.flush()
        try:
            os.link(temporary.name, target)
        except FileExistsError:
            pass


def _copy_missing(source: Path, target: Path) -> None:
    if _present(target):
        return
    if source.is_symlink():
        raise OSError(f"Cannot initialize development configuration from a symlink: {source}")
    _write_missing(target, source.read_bytes())


def initialize_configuration(path: Path, data_root: Path) -> None:
    """Copy user resources and API keys once, without copying application history."""
    if _present(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise OSError(f"Development configuration destination must not be a symlink: {path.parent}")
    # Two development launchers must not observe a partially initialized tree.
    with FileLock(str(path.parent / ".initialize.lock"), timeout=10, mode=0o600):
        if _present(path):
            return
        source = default_harness_ui_settings_path()
        if not _present(source):
            _write_missing(path, b'schema_version: "1"\n')
            print(f"Initialized {path}; complete setup to select a Model.", file=sys.stderr)
            return
        for directory, suffixes in RESOURCE_SUFFIXES.items():
            source_directory = source.parent / directory
            if source_directory.is_symlink():
                raise OSError(f"Cannot initialize development configuration from a symlink: {source_directory}")
            if not source_directory.exists():
                continue
            for resource in sorted(source_directory.iterdir()):
                if resource.suffix in suffixes and not (directory == "subagents" and resource.name == "README.md"):
                    _copy_missing(resource, path.parent / directory / resource.name)
        guidance = source.parent / "AGENTS.md"
        if _present(guidance):
            _copy_missing(guidance, path.parent / guidance.name)
        # API-key references are data-root-local; subscription stores remain product-owned.
        keys = resolve_harness_ui_data_root(source) / "auth.json"
        if _present(keys):
            _copy_missing(keys, data_root / "auth.json")
        # Publish the root last so a failed copy can be retried without treating it as complete.
        _copy_missing(source, path)
        print(
            f"Initialized {path} from {source}; existing files and application history are unchanged.", file=sys.stderr
        )


def main() -> None:
    arguments = sys.argv[1:]
    path = ROOT / "var/harness-ui/a13n-harness-ui.yaml"
    data_root = ROOT / "var/harness-ui/data"
    try:
        # Reuse the real global-option parser, including --config=PATH and repeated options.
        # Root help/version exit here; subcommand help must also avoid seeding private files.
        with cli.make_context("a13n-harness-ui", arguments.copy()) as context:
            if context.params["config_path"] is None and not {"--help", "-h"}.intersection(context.args):
                selected_data = context.params["data_root"]
                initialize_configuration(
                    path,
                    resolve_harness_ui_data_root(
                        path, data_root=selected_data if isinstance(selected_data, Path) else data_root
                    ),
                )
    except click.exceptions.Exit as exc:
        raise SystemExit(exc.exit_code) from None
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from None
    except (OSError, Timeout) as exc:
        print(f"Cannot initialize Harness UI development configuration: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    cli.main(
        args=["--no-update-check", "--config", str(path), "--data-root", str(data_root), *arguments],
        prog_name="a13n-harness-ui",
    )


if __name__ == "__main__":
    main()
