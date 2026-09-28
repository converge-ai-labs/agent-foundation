"""Operate this checkout's local Service instance; dev/service/README.md describes each command.

Only the standard library is imported up front: `status`, `stop` and `down` run on any Python 3.10+, and the
other commands first synchronize the repository environment, then continue inside it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

from dev.service import lifecycle, stores
from dev.service.applications import applications, create_administrator, migrate
from dev.service.checkout import ADMIN_EMAIL, ADMIN_PASSWORD, ROOT, Checkout, Section
from dev.service.instance import instance_file, require_free

ENVIRONMENT = ROOT / ".venv"


def main() -> None:
    args = _parser().parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # interleave with subprocess output in order
    try:
        if args.command == "status":
            print(json.dumps(status(ROOT), indent=2))
        elif args.command == "stop":
            print("Stopped the applications." if lifecycle.stop_applications(ROOT) else "Applications are not running.")
        elif args.command == "down":
            down(ROOT)
        elif Path(sys.prefix).resolve() != ENVIRONMENT.resolve():
            _enter_environment(console=args.command == "dev")
        elif args.command == "dev" and args.lock_fd is not None:
            _run_detached_applications(Checkout.resolve(ROOT), args.lock_fd)
        else:
            COMMANDS[args.command](Checkout.resolve(ROOT), args)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        failed = (
            f"Command failed: {' '.join(map(str, error.cmd))}"
            if isinstance(error, subprocess.CalledProcessError)
            else error
        )
        print(failed, file=sys.stderr)
        raise SystemExit(1) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m dev.service", description=__doc__)
    parser.add_argument(
        "--traces",
        choices=("auto", "langfuse", "none"),
        default="auto",
        help="Langfuse export: auto uses the shared stack only when it already runs; langfuse starts it",
    )
    # The inherited lifecycle lock of a detached `dev` supervisor.
    parser.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    dev = commands.add_parser("dev", help="prepare, then run the model, Service and Console in the background")
    dev.add_argument("--foreground", action="store_true", help="stay attached; Ctrl+C stops everything")
    commands.add_parser("service-dev", help="prepare, then run the model and Service in the foreground")
    commands.add_parser("setup", help="prepare stores, schema and administrator without starting applications")
    commands.add_parser("status", help="print this checkout's instance as JSON without changing anything")
    commands.add_parser("stop", help="stop running applications")
    commands.add_parser("down", help="stop this checkout's stores, keeping their data")
    reset = commands.add_parser("reset", help="delete this checkout's state and rebuild it")
    reset.add_argument("state", choices=("empty", "seeded"))
    return parser


def _enter_environment(*, console: bool) -> None:
    """Synchronize locked dependencies (fast when unchanged), then rerun this command in the repository venv."""
    try:
        subprocess.run(["uv", "sync", "--locked", "--quiet", "--all-packages"], cwd=ROOT, check=True)
        if console:
            frontend = ["pnpm", "--dir", str(ROOT / "frontend"), "install", "--frozen-lockfile", "--reporter=silent"]
            subprocess.run(frontend, check=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"{error.filename} is required; see CONTRIBUTING.md#local-setup") from None
    python = ENVIRONMENT / "bin/python"
    os.execv(python, [str(python), "-m", "dev.service", *sys.argv[1:]])


def status(root: Path) -> dict[str, object]:
    checkout = Checkout.load(root)
    if checkout is None:
        return {"configured": False, "instance_file": str(instance_file(root))}
    ports = checkout.instance.ports.named()
    return {
        "configured": True,
        "instance": checkout.id,
        "console_url": checkout.console_url,
        "service_url": checkout.service_url,
        "login": {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        "ports": ports,
        "listeners": {name: lifecycle.listening(port) for name, port in ports.items()},
        "owner": lifecycle.owner(checkout.root),
        "seeded": checkout.seed_report.exists(),
        "settings": str(checkout.settings_file),
        "logs": str(checkout.logs),
    }


def down(root: Path) -> None:
    checkout = Checkout.load(root)
    if checkout is None:
        print("This checkout has no local instance.")
        return
    with lifecycle.owning(checkout.root, "down"):
        stores.stop(checkout.instance)


def setup(checkout: Checkout, args: argparse.Namespace) -> None:
    with lifecycle.owning(checkout.root, "setup"):
        _prepare(checkout, args.traces)
    print("Stores, schema and administrator are ready; applications are not running (make dev starts them).")


def dev(checkout: Checkout, args: argparse.Namespace) -> None:
    selected = applications(checkout, console=True)
    with lifecycle.owning(checkout.root, "dev") as lock:
        _require_free(selected)
        _prepare(checkout, args.traces)
        if args.foreground:
            lifecycle.claim(lock, "dev-foreground")
            raise SystemExit(
                lifecycle.supervise(checkout.root, selected, on_ready=partial(_ready, checkout, console=True))
            )
        command = (sys.executable, "-m", "dev.service", "--lock-fd", str(lock), "dev")
        ports = tuple(application.port for application in selected)
        lifecycle.start_detached(checkout.root, command, lock, ports, checkout.logs / "supervisor.log")
    _ready(checkout, console=True, detached=True)


def _run_detached_applications(checkout: Checkout, lock: int) -> None:
    lifecycle.claim(lock, "dev")
    raise SystemExit(lifecycle.supervise(checkout.root, applications(checkout, console=True), logs=checkout.logs))


def service_dev(checkout: Checkout, args: argparse.Namespace) -> None:
    selected = applications(checkout, console=False)
    with lifecycle.owning(checkout.root, "service-dev"):
        _require_free(selected)
        _prepare(checkout, args.traces)
        raise SystemExit(
            lifecycle.supervise(checkout.root, selected, on_ready=partial(_ready, checkout, console=False))
        )


def reset(checkout: Checkout, args: argparse.Namespace) -> None:
    if checkout.state.resolve() != checkout.state:
        raise ValueError(f"{checkout.state} must not traverse a symlink")
    with lifecycle.owning(checkout.root, "reset"):
        if args.state == "seeded":
            _require_free(applications(checkout, console=False))
        stores.delete(checkout.instance)
        for directory in (checkout.objects, checkout.environments):
            shutil.rmtree(directory, ignore_errors=True)
        for path in (checkout.seed_report, checkout.state / "dev-resources.json", checkout.mem0_records):
            path.unlink(missing_ok=True)
        _prepare(checkout, args.traces)
        if args.state == "seeded":
            _seed(checkout)
    print(f"Reset this checkout to the {args.state} state.")


COMMANDS = {"dev": dev, "service-dev": service_dev, "setup": setup, "reset": reset}


def _require_free(selected: tuple[lifecycle.Application, ...]) -> None:
    require_free({application.name: application.port for application in selected})


def _prepare(checkout: Checkout, traces: str) -> None:
    """Write the settings, start the stores, migrate, and create the administrator of an empty database."""
    checkout.write_settings(_telemetry(checkout, traces))
    stores.start(checkout.instance)
    migrate(checkout)
    if create_administrator(checkout):
        print(f"Created the local administrator {ADMIN_EMAIL}.")
    print(f"Settings: {checkout.settings_file}")


def _telemetry(checkout: Checkout, mode: str) -> Section:
    """Export to the machine-shared Langfuse when selected, or when `auto` finds it already running."""
    from dev.observability.langfuse import PUBLIC_KEY, SECRET_KEY, Langfuse

    stack = Langfuse()
    if mode == "none":
        print("Traces: off")
        return {"trace_backend": "none"}
    if mode == "auto" and not lifecycle.listening(stack.port):
        print("Traces: off; after make langfuse-up, a restart exports them to the shared Langfuse")
        return {"trace_backend": "none"}
    if mode == "langfuse":
        stack.start()
    try:
        stack.check_credentials()
    except RuntimeError as error:
        if mode == "langfuse":
            raise
        print(f"Traces: off ({error})")
        return {"trace_backend": "none"}
    print(f"Traces: {stack.base_url}, environment {checkout.trace_environment}")
    keys = {"langfuse_public_key": PUBLIC_KEY, "langfuse_secret_key": SECRET_KEY}
    return {"trace_backend": "langfuse", "trace_url": stack.base_url, **keys}


def _seed(checkout: Checkout) -> None:
    from dev.service.api import Api
    from dev.service.seed import seed, write_report
    from dev.service.seed_verify import verify

    selected = applications(checkout, console=False)
    with lifecycle.running(checkout.root, selected, checkout.logs), Api(checkout.service_url) as api:
        api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
        seeded = seed(api, checkout)
        checks = verify(api, seeded)
        if failed := [name for name, passed in checks if not passed]:
            raise RuntimeError("Seed verification failed: " + "; ".join(failed))
        write_report(checkout.seed_report, checkout.console_url, seeded, checks)
        print(f"Seeded and verified: {checkout.seed_report}")
        _apply_private_resources(checkout)


def _apply_private_resources(checkout: Checkout) -> None:
    """Optional: a failure leaves the seeded state and the running applications usable."""
    import httpx2

    from dev.service.dev_resources import apply_to

    try:
        summary = apply_to(checkout)
    except (ValueError, RuntimeError, httpx2.HTTPError) as error:
        print(f"Private development resources were not applied: {error}", file=sys.stderr)
        return
    if summary:
        print(f"Private development resources: {summary}")


def _ready(checkout: Checkout, *, console: bool, detached: bool = False) -> None:
    _apply_private_resources(checkout)
    if console:
        print(f"\nConsole: {checkout.console_url}\nSign in: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    print(f"Service API: {checkout.service_url}")
    if detached:
        print(f"Running in the background; logs in {checkout.logs}; stop with make dev-stop.")
    if not checkout.seed_report.exists():
        print("For fictional content: make dev-reset STATE=seeded (replaces this checkout's data).")


if __name__ == "__main__":
    main()
