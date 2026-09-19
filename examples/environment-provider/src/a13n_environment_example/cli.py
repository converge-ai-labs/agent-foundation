"""Command-line entry point for the built-in Environment Provider examples."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .application import (
    DEFAULT_EXAMPLE_DOCKER_IMAGE,
    StatelessExampleResult,
    run_direct_local,
    run_docker,
    run_local_envd,
)

_DEFAULT_ROOT = Path(".environment-provider-example")


def main() -> None:
    """Run one selected Provider lifecycle."""

    arguments = _parser().parse_args()
    asyncio.run(_run(arguments))


async def _run(arguments: argparse.Namespace) -> None:
    if arguments.provider == "direct_local":
        result = await run_direct_local(arguments.workspace)
        _print_stateless(result)
        return
    if arguments.provider == "local_envd":
        result = await run_local_envd(
            arguments.workspace,
            executable=arguments.executable,
        )
        _print_stateless(result)
        return
    if arguments.provider == "docker":
        result = await run_docker(
            image=arguments.image,
        )
        print(f"provider: {result.provider_key}")
        print(f"environment: {result.environment_id}")
        print(f"first read: {result.first_text.strip()}")
        print(f"re-entry read: {result.reentered_text.strip()}")
        print(f"state version: {result.state_version}")
        print(f"target destroyed: {result.destroyed}")
        return
    if arguments.provider in {"http_envd", "websocket_envd", "remote_envd_demo"}:
        from pydantic import SecretStr

        from .remote import run_http, run_websocket
        from .remote_demo import run_demo

        if arguments.provider == "remote_envd_demo":
            remote = await run_demo(arguments.executable, arguments.transport)
        else:
            token = SecretStr((await asyncio.to_thread(arguments.credential_file.read_text)).strip())
            if arguments.provider == "http_envd":
                remote = await run_http(arguments.endpoint, token, arguments.daemon_environment_id)
            else:
                remote = await run_websocket(
                    token=token, daemon_environment_id=arguments.daemon_environment_id, port=arguments.port
                )
        print(f"provider: {remote.provider_key}")
        print(f"re-entry read: {remote.text.strip()}")
        print(f"same daemon generation: {remote.same_generation}")
        print("provider close preserved remote daemon and workspace")
        if arguments.provider == "remote_envd_demo":
            print("demo operator cleaned up its temporary daemon and workspace")
        return
    raise ValueError(f"Unsupported Provider example: {arguments.provider}")


def _print_stateless(result: StatelessExampleResult) -> None:
    print(f"provider: {result.provider_key}")
    print(f"environment: {result.environment_id}")
    print(f"read: {result.text.strip()}")
    print(f"state: {'none' if result.state is None else result.state.state_version}")
    print(f"workspace preserved: {result.workspace_preserved}")
    print(f"workspace: {result.workspace}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="environment-provider-example",
        description="Run Host-side lifecycle examples for built-in Environment Providers.",
    )
    providers = parser.add_subparsers(dest="provider", required=True)

    direct_local = providers.add_parser(
        "direct_local",
        help="Use Direct Local against a Host-owned workspace.",
    )
    direct_local.add_argument(
        "--workspace",
        type=Path,
        default=_DEFAULT_ROOT / "direct-local-workspace",
    )

    local_envd = providers.add_parser(
        "local_envd",
        help="Launch a private a13n-envd generation over a Host-owned workspace.",
    )
    local_envd.add_argument(
        "--workspace",
        type=Path,
        default=_DEFAULT_ROOT / "local-envd-workspace",
    )
    local_envd.add_argument(
        "--executable",
        type=Path,
        help="Exact a13n-envd executable; otherwise use A13N_ENVD_EXECUTABLE or PATH.",
    )

    docker = providers.add_parser(
        "docker",
        help="Create, re-enter, and explicitly destroy one Docker Environment.",
    )
    docker.add_argument(
        "--image",
        default=DEFAULT_EXAMPLE_DOCKER_IMAGE,
        help="Sandbox image reference; defaults to the image built by make image-docker-environment.",
    )
    for name in ("http_envd", "websocket_envd"):
        remote = providers.add_parser(name, help="Connect to an externally operated daemon; never destroy its target.")
        remote.add_argument(
            "--daemon-environment-id", required=True, help="Exact A13N_ENVD_ENVIRONMENT_ID configured by the operator."
        )
        remote.add_argument(
            "--credential-file",
            type=Path,
            required=True,
            help="Protected token file; never put credentials on the command line.",
        )
        if name == "http_envd":
            remote.add_argument("--endpoint", required=True, help="EIP HTTP(S) origin without a path.")
        else:
            remote.add_argument(
                "--port", type=int, default=8788, help="Loopback port for this example Host's listener."
            )
    demo = providers.add_parser(
        "remote_envd_demo", help="Try a remote Provider with a temporary local daemon owned by demo Host code."
    )
    demo.add_argument("--executable", type=Path, required=True)
    demo.add_argument("--transport", choices=("http", "websocket"), default="http")
    return parser


if __name__ == "__main__":
    main()
