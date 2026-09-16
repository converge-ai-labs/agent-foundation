"""Operate one isolated local Service development instance."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from .instance import Instance, ensure_instance, load_instance
from .lifecycle import (
    ProcessSpec,
    background_applications,
    inherited_lifecycle_lock,
    lifecycle_lock,
    stop_background_applications,
    supervise,
)
from .preparation import prepare

if TYPE_CHECKING:
    from .environment import Environment
    from .langfuse import Langfuse
    from .mem0 import Mem0Settings

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG = ROOT / "dev/service/local.toml"
LOCAL_MEM0_CONFIG = ROOT / "dev/mem0/local.toml"
LOCKED_COMMANDS = {"dev", "service-dev", "setup", "down", "reset", "mem0"}
LISTENER_COMMANDS = {"dev", "service-dev", "setup"}
CHILD_OWNER = "A13N_DEV_CHILD_OWNER"
APPLICATION_LOG = Path("var/dev/applications.log")


def check_ports(instance: Instance, *, console: bool = False) -> None:
    selected = [(instance.ports.service, "Service"), (instance.ports.model, "scripted model")]
    if console:
        selected.append((instance.ports.console, "Console"))
    for port, name in selected:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                raise ValueError(
                    f"{name} port 127.0.0.1:{port} is in use; this checkout keeps stable ports and will not reassign it"
                ) from None


def setup(
    environment: Environment,
    langfuse: Langfuse,
    config: Path,
    *,
    mem0_settings: Mem0Settings,
) -> None:
    """Prepare infrastructure while the CLI owns the lifecycle lock."""
    from a13n_service.database import DatabaseMigrator

    from .docker import ensure_docker
    from .langfuse import USER_EMAIL, USER_PASSWORD
    from .mem0 import Mem0

    started = time.monotonic()
    if environment.incomplete.exists():
        raise ValueError("The previous reset did not complete; rerun make dev-reset with the intended STATE")
    langfuse.validate()
    mem0 = Mem0(environment, mem0_settings)
    if mem0.settings.enabled:
        mem0.validate()
    ensure_docker()
    environment.report_legacy_resources()
    phase = time.monotonic()
    environment.compose("up", "-d", "--wait")
    print(f"Infrastructure Service stores: {time.monotonic() - phase:.2f}s", flush=True)
    phase = time.monotonic()
    langfuse.start()
    print(f"Infrastructure shared Langfuse: {time.monotonic() - phase:.2f}s", flush=True)
    if mem0.settings.enabled:
        mem0.start()
    phase = time.monotonic()
    DatabaseMigrator(environment.settings.database_config(), environment.settings.migration_config()).upgrade()
    print(f"Schema migration: {time.monotonic() - phase:.2f}s", flush=True)
    print(f"Setup total: {time.monotonic() - started:.2f}s")
    print(f"Configuration: {config.resolve()}")
    print(f"Instance: {environment.local_id}")
    print(f"Service: http://127.0.0.1:{environment.ports.service}")
    print(f"Console: http://127.0.0.1:{environment.ports.console}")
    if langfuse.enabled:
        print(f"Shared Langfuse: {langfuse.base_url} (media: http://127.0.0.1:{langfuse.port + 1})")
        print(f"Public local Langfuse account: {USER_EMAIL} / {USER_PASSWORD}")
    print("Infrastructure and schema ready; Service and Console have not been started.")
    if not (environment.state / "seed.json").exists():
        print("For fictional content, explicitly run make dev-reset STATE=seeded; this deletes this instance's data.")


def _serve(environment: Environment, langfuse: Langfuse) -> None:
    from a13n_service.app import create_app
    from a13n_service.log import configure_logging
    from a13n_service.process.server import serve_app

    from .langfuse import local_traces
    from .model import model_process

    with local_traces(langfuse):
        if environment.incomplete.exists():
            raise ValueError("The previous reset did not complete; run reset again before starting Service")
        configure_logging(environment.settings)
        with model_process(environment.ports.model):
            serve_app(create_app(environment.settings))


def _console(environment: Environment) -> None:
    ports = environment.ports
    os.execvpe(
        "pnpm",
        ["pnpm", "--dir", str(ROOT / "frontend"), "--filter", "a13n-console", "dev", "--port", str(ports.console)],
        {**os.environ, "A13N_CONSOLE_SERVICE_URL": f"http://127.0.0.1:{ports.service}"},
    )


def _run_dev(environment: Environment, config: Path, mem0_config: Path, *, detached: bool = False) -> None:
    owner = str(os.getpid())
    command = [
        sys.executable,
        "-m",
        "dev.service",
        "--instance-root",
        str(environment.root),
        "--config",
        str(config),
        "--mem0-config",
        str(mem0_config),
    ]
    child_environment = {**os.environ, CHILD_OWNER: owner}
    if detached:
        print("Starting detached a13n Service and Console.", flush=True)
    else:
        print("Starting a13n Service and Console. Press Ctrl+C to stop both.", flush=True)
    signum = supervise(
        ROOT,
        (
            ProcessSpec("a13n Service", (*command, "_serve"), child_environment),
            ProcessSpec("Console", (*command, "_console"), child_environment),
        ),
    )
    if signum is not None:
        raise SystemExit(128 + signum)


def _wait_for_background_start(pid: int, environment: Environment, log: Path) -> None:
    ports = (environment.ports.service, environment.ports.model, environment.ports.console)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        exited, status = os.waitpid(pid, os.WNOHANG)
        if exited:
            raise RuntimeError(f"Detached development applications exited during startup (status {status}); see {log}")
        if all(_port_in_use(port) for port in ports):
            print(f"Development applications are running in the background. Logs: {log}")
            print(f"Stop them with make dev-stop. Console: http://127.0.0.1:{environment.ports.console}")
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)
    raise RuntimeError(f"Timed out starting detached development applications; see {log}")


def _run_detached(environment: Environment, config: Path, mem0_config: Path) -> None:
    log = environment.root / APPLICATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid:
        _wait_for_background_start(pid, environment, log)
        return

    status = 0
    try:
        os.setsid()
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        input_fd = os.open(os.devnull, os.O_RDONLY)
        log_fd = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.dup2(input_fd, sys.stdin.fileno())
            os.dup2(log_fd, sys.stdout.fileno())
            os.dup2(log_fd, sys.stderr.fileno())
        finally:
            os.close(input_fd)
            os.close(log_fd)
        with background_applications(environment.root):
            _run_dev(environment, config, mem0_config, detached=True)
    except SystemExit as error:
        status = error.code if isinstance(error.code, int) else 1
    except BaseException:
        traceback.print_exc()
        status = 1
    finally:
        os._exit(status)


def _port_in_use(port: int) -> bool:
    with socket.socket() as client:
        client.settimeout(0.1)
        return client.connect_ex(("127.0.0.1", port)) == 0


def _status(root: Path) -> None:
    instance = load_instance(root)
    if instance is None:
        print(json.dumps({"configured": False, "instance_file": str(root / "var/dev/instance.json")}))
        return
    port_values = asdict(instance.ports)
    print(
        json.dumps(
            {
                "configured": True,
                "instance": instance.id,
                "ports": port_values,
                "listeners": {name: _port_in_use(port) for name, port in port_values.items()},
                "service_url": f"http://127.0.0.1:{instance.ports.service}",
                "console_url": f"http://127.0.0.1:{instance.ports.console}",
                "state": str(root / "var/dev/service"),
                "reset_incomplete": (root / "var/dev/reset-incomplete").exists(),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    parser.add_argument("--mem0-config", type=Path, default=LOCAL_MEM0_CONFIG)
    parser.add_argument("--prepared", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prepared-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--instance-root", type=Path, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("dev")
    command.add_argument("--foreground", action="store_true")
    for command in ("service-dev", "setup", "status", "stop", "down", "check-ports"):
        commands.add_parser(command)
    command = commands.add_parser("reset")
    command.add_argument("state", choices=("empty", "seeded"))
    command = commands.add_parser("langfuse")
    command.add_argument("action", choices=("up", "down", "reset", "test"))
    command = commands.add_parser("mem0")
    command.add_argument("action", choices=("up", "down", "logs"))
    commands.add_parser("_serve", help=argparse.SUPPRESS)
    commands.add_parser("_console", help=argparse.SUPPRESS)
    return parser


def _exec_prepared(lock_fd: int | None) -> None:
    arguments = [str(ROOT / ".venv/bin/python"), "-m", "dev.service", "--prepared"]
    if lock_fd is not None:
        arguments.extend(("--prepared-fd", str(lock_fd)))
    arguments.extend(sys.argv[1:])
    os.execve(arguments[0], arguments, os.environ)


def _bootstrap(args: argparse.Namespace, root: Path) -> None:
    instance = ensure_instance(root)
    if args.command in LOCKED_COMMANDS:
        with lifecycle_lock(root, inheritable=True) as lock_fd:
            if args.command in LISTENER_COMMANDS:
                check_ports(instance, console=args.command == "dev")
            prepare(ROOT, console=args.command == "dev")
            _exec_prepared(lock_fd)
    prepare(ROOT, console=False)
    _exec_prepared(None)


def _environment(config: Path, root: Path):
    from .resolution import resolve_environment

    environment = resolve_environment(config, root=root)
    assert environment is not None
    environment.validate()
    return environment


def _run_prepared(args: argparse.Namespace, root: Path) -> None:
    from .docker import ensure_docker
    from .langfuse import USER_EMAIL, USER_PASSWORD, Langfuse, local_traces
    from .mem0 import Mem0, load_mem0_settings
    from .reset import reset

    environment = _environment(args.config, root)
    langfuse = Langfuse(environment)
    langfuse.validate()
    mem0_settings = load_mem0_settings(args.mem0_config)
    if args.command in {"dev", "service-dev", "setup"}:
        setup(environment, langfuse, args.config, mem0_settings=mem0_settings)
    if args.command == "dev":
        if args.foreground:
            _run_dev(environment, args.config, args.mem0_config)
        else:
            _run_detached(environment, args.config, args.mem0_config)
    elif args.command == "service-dev":
        _serve(environment, langfuse)
    elif args.command == "reset":
        ensure_docker()
        if args.state == "seeded":
            langfuse.start()
        with local_traces(langfuse):
            reset(environment, args.state)
    elif args.command == "down":
        ensure_docker()
        environment.require_stopped()
        environment.compose("stop")
        Mem0(environment, mem0_settings).compose("stop")
    elif args.command == "mem0":
        ensure_docker()
        mem0 = Mem0(environment, mem0_settings)
        if args.action == "up":
            if not mem0.settings.enabled:
                raise ValueError("Mem0 is disabled in the selected --mem0-config file")
            mem0.start()
        elif args.action == "logs":
            mem0.compose("logs", "--tail", "100", "mem0")
        else:
            mem0.compose("down")
    elif args.command == "langfuse":
        if args.action == "up":
            langfuse.start()
            print(f"Shared Langfuse: {langfuse.base_url}\nPublic local account: {USER_EMAIL} / {USER_PASSWORD}")
        elif args.action == "test":
            langfuse.test()
        else:
            langfuse.stop(reset=args.action == "reset")


def main(*, instance_root: Path = ROOT) -> None:
    args = _parser().parse_args()
    root = (args.instance_root or instance_root).resolve()
    try:
        if args.command == "status":
            _status(root)
            return
        if args.command == "stop":
            if stop_background_applications(root):
                print("Stopped detached development applications.")
            else:
                print("Detached development applications are not running.")
            return
        if args.command == "check-ports":
            check_ports(ensure_instance(root), console=True)
            return
        if args.command in {"_serve", "_console"}:
            if os.environ.get(CHILD_OWNER) != str(os.getppid()):
                raise ValueError("Internal development child commands require their lifecycle owner")
            environment = _environment(args.config, root)
            from .langfuse import Langfuse

            langfuse = Langfuse(environment)
            (_serve(environment, langfuse) if args.command == "_serve" else _console(environment))
            return
        if not args.prepared:
            _bootstrap(args, root)
            return
        if args.command in LOCKED_COMMANDS:
            if args.prepared_fd is None:
                raise ValueError("Prepared local command is missing its lifecycle lock")
            with inherited_lifecycle_lock(args.prepared_fd):
                _run_prepared(args, root)
        else:
            _run_prepared(args, root)
    except (ValueError, RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(str(error) if isinstance(error, (ValueError, RuntimeError)) else "Local command failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
