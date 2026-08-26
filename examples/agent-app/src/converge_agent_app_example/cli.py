"""Run one progressive layer of the Agent application example."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .application import run_basic_agent
from .recovery import print_host_recovery_result, run_host_recovery
from .workspace import print_local_workspace_result, run_local_workspace

_DEFAULT_PROMPT = "Explain what this Agent application demonstrates."


def create_demo_model() -> FunctionModel:
    """Return a deterministic replacement for the external model provider."""

    async def respond(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "This normal Python application built and ran an Agent through Agent Harness."

    return FunctionModel(stream_function=respond)


async def _run_selected_layer(arguments: argparse.Namespace) -> None:
    if arguments.layer in {None, "basic"}:
        basic_result = await run_basic_agent(
            arguments.prompt,
            model=create_demo_model(),
        )
        print(f"output: {basic_result.output_or_raise()}")
        print(f"thread_id: {basic_result.thread_id}")
        print(f"run_id: {basic_result.run_id}")
        return

    if arguments.layer == "local":
        if arguments.workspace is not None:
            local_result = await run_local_workspace(
                arguments.workspace.resolve(),
                review_style=arguments.review_style,
            )
            print_local_workspace_result(local_result)
            return
        with TemporaryDirectory(prefix="converge-agent-app-local-") as workspace_directory:
            local_result = await run_local_workspace(
                Path(workspace_directory),
                review_style=arguments.review_style,
            )
            print_local_workspace_result(local_result)
        return

    if arguments.state_dir is not None:
        recovery_result = await run_host_recovery(arguments.state_dir.resolve())
        print_host_recovery_result(recovery_result)
        return
    with TemporaryDirectory(prefix="converge-agent-app-host-") as state_directory:
        recovery_result = await run_host_recovery(Path(state_directory))
        print_host_recovery_result(recovery_result)


def _create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="layer")

    basic_parser = subparsers.add_parser("basic", help="Run the minimal embedded Harness path")
    basic_parser.add_argument("prompt", nargs="?", default=_DEFAULT_PROMPT)

    local_parser = subparsers.add_parser("local", help="Add local Capabilities and a Direct Local Environment")
    local_parser.add_argument("--workspace", type=Path, help="Retain the Direct Local workspace in this directory")
    local_parser.add_argument("--review-style", choices=("Focused", "Broad"), default="Focused")

    host_parser = subparsers.add_parser("host", help="Add Host-owned persistence, fencing, and recovery")
    host_parser.add_argument("--state-dir", type=Path, help="Retain Host state in this directory")

    parser.set_defaults(prompt=_DEFAULT_PROMPT)
    return parser


def main() -> None:
    arguments = _create_argument_parser().parse_args()
    asyncio.run(_run_selected_layer(arguments))


if __name__ == "__main__":
    main()
