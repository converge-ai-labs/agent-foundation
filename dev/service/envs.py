"""List and remove checkout-owned local development environments."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .instance import Instance, _identity, _machine_lock, _parse_instance, _read, _write_json, instance_path
from .lifecycle import lifecycle_lock, stop_background_applications
from .state import machine_directory

ROOT = Path(__file__).resolve().parents[2]
PROJECT_PREFIX = "a13n-dev-v2-"


def _git_worktrees() -> dict[str, str | None]:
    output = subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    worktrees: dict[str, str | None] = {}
    path: str | None = None
    branch: str | None = None
    for line in (*output.splitlines(), ""):
        if not line:
            if path is not None:
                worktrees[path] = branch
            path = branch = None
        elif line.startswith("worktree "):
            path = str(Path(line[9:]).resolve())
        elif line.startswith("branch "):
            branch = line[7:].removeprefix("refs/heads/")
    return worktrees


def _registry() -> dict[str, Instance]:
    path = machine_directory() / "instances.json"
    if not path.exists():
        return {}
    records = _read(path)
    result: dict[str, Instance] = {}
    for root, value in records.items():
        instance = _parse_instance(value, path)
        if instance.root != root or instance.id != _identity(Path(root)):
            raise ValueError(f"Invalid machine registry ownership for checkout: {root}")
        result[root] = instance
    return result


def _docker_projects() -> tuple[dict[str, str], dict[str, int]]:
    result = subprocess.run(
        ["docker", "compose", "ls", "--all", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    projects = {item["Name"]: item["Status"] for item in json.loads(result.stdout or "[]")}
    volumes = subprocess.run(
        ["docker", "volume", "ls", "--filter", "label=com.docker.compose.project", "--format", "{{json .}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    counts: dict[str, int] = {}
    for line in volumes.stdout.splitlines():
        labels = json.loads(line).get("Labels", "")
        if isinstance(labels, str):
            project = next(
                (
                    part.partition("=")[2]
                    for part in labels.split(",")
                    if part.startswith("com.docker.compose.project=")
                ),
                None,
            )
        else:
            project = labels.get("com.docker.compose.project")
        if project:
            counts[project] = counts.get(project, 0) + 1
    return projects, counts


def list_environments() -> list[dict]:
    worktrees = _git_worktrees()
    registry = _registry()
    projects, volumes = _docker_projects()
    rows = []
    for root in sorted(set(worktrees) | set(registry)):
        instance = registry.get(root)
        project = PROJECT_PREFIX + instance.id if instance else None
        rows.append(
            {
                "root": root,
                "branch": worktrees.get(root),
                "worktree": root in worktrees,
                "instance": instance.id if instance else None,
                "ports": asdict(instance.ports) if instance else None,
                "service": projects.get(project) if project else None,
                "mem0": projects.get(project + "-mem0") if project else None,
                "state": (Path(root) / "var/dev").exists() if instance else False,
                "volumes": volumes.get(project, 0) + volumes.get(project + "-mem0", 0) if project else 0,
            }
        )
    known = {PROJECT_PREFIX + instance.id for instance in registry.values()}
    orphan_projects = {
        project.removesuffix("-mem0")
        for project in set(projects) | set(volumes)
        if project.startswith(PROJECT_PREFIX) and project.removesuffix("-mem0") not in known
    }
    for project in sorted(orphan_projects):
        rows.append(
            {
                "root": None,
                "branch": None,
                "worktree": False,
                "instance": project.removeprefix(PROJECT_PREFIX),
                "ports": None,
                "service": projects.get(project),
                "mem0": projects.get(project + "-mem0"),
                "state": False,
                "volumes": volumes.get(project, 0) + volumes.get(project + "-mem0", 0),
                "unregistered": True,
            }
        )
    return rows


def _compose_down(instance: Instance, *, mem0: bool) -> None:
    project = PROJECT_PREFIX + instance.id + ("-mem0" if mem0 else "")
    compose = ROOT / ("dev/mem0/compose.yaml" if mem0 else "dev/service/compose.yaml")
    env = {
        **os.environ,
        "A13N_DEV_POSTGRES_PORT": str(instance.ports.postgres),
        "A13N_DEV_REDIS_PORT": str(instance.ports.redis),
        "MEM0_LOCAL_PORT": str(instance.ports.mem0),
        "MEM0_LOCAL_API_KEY": "local-mem0-api-key",
    }
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "--project-name",
            project,
            "--file",
            str(compose),
            "down",
            "--volumes",
            "--remove-orphans",
        ],
        env=env,
        check=True,
    )


def _remove(instance: Instance) -> None:
    root = Path(instance.root)
    state = root / "var/dev"
    if not root.is_dir() or root.is_symlink() or state.is_symlink():
        raise ValueError(f"Checkout or local state is missing or symlinked: {root}")
    stop_background_applications(root)
    with lifecycle_lock(root):
        with _machine_lock():
            current = _registry().get(str(root))
            if current != instance:
                raise ValueError(f"Instance registration changed: {root}")
            local = instance_path(root)
            if not local.is_file() or local.is_symlink() or _parse_instance(_read(local), local) != instance:
                raise ValueError(f"Instance file does not match registration: {local}")
            _compose_down(instance, mem0=True)
            _compose_down(instance, mem0=False)
            shutil.rmtree(state)
            records = _registry()
            del records[str(root)]
            _write_json(
                machine_directory() / "instances.json", {path: asdict(value) for path, value in records.items()}
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listed = commands.add_parser("list", help="List worktrees and local test environments")
    listed.add_argument("--json", action="store_true")
    removed = commands.add_parser("rm", help="Remove selected registered environments and their local data")
    removed.add_argument("ids", nargs="+", help="Instance IDs from list")
    removed.add_argument("--dry-run", action="store_true")
    removed.add_argument("--yes", action="store_true", help="Confirm deletion")
    args = parser.parse_args()
    try:
        if args.command == "list":
            rows = list_environments()
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                print(f"{'INSTANCE':12} {'SERVICE':12} {'MEM0':12} {'VOL':>3} {'BRANCH':40} PATH")
                for row in rows:
                    print(
                        f"{row['instance'] or '-':12} {row['service'] or '-':12} {row['mem0'] or '-':12} {row['volumes']:>3} {row['branch'] or '-':40} {row['root'] or '(unregistered Docker project)'}"
                    )
            return
        registry = _registry()
        by_id = {instance.id: instance for instance in registry.values()}
        if len(set(args.ids)) != len(args.ids):
            raise ValueError("Duplicate instance ID")
        missing = [item for item in args.ids if item not in by_id]
        if missing:
            raise ValueError("Unknown or unregistered instance ID: " + ", ".join(missing))
        for item in args.ids:
            instance = by_id[item]
            print(
                f"{item}: remove Service and Mem0 containers/volumes, {instance.root}/var/dev, and instance registration"
            )
        if args.dry_run:
            return
        if not args.yes:
            raise ValueError("Pass --yes to confirm deletion")
        failures = []
        for item in args.ids:
            try:
                _remove(by_id[item])
                print(f"{item}: removed")
            except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
                failures.append(item)
                print(f"{item}: failed: {error}", file=sys.stderr)
        if failures:
            raise ValueError("Could not remove: " + ", ".join(failures))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
