"""Run the recoverable streaming conversation example."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .application import ConversationApplication
from .environment import create_demo_environment

_DEFAULT_STATE_PATH = Path(".agent-app/conversation-state.json")


def create_demo_model() -> FunctionModel:
    """Create an offline model that proves earlier turns reached each request."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        turn_count = sum(isinstance(message, ModelRequest) for message in messages)
        yield f"Turn {turn_count}: "
        yield f"the model received the complete conversation through turn {turn_count}."

    return FunctionModel(stream_function=stream, model_name="offline-conversation-model")


async def _print_turn(application: ConversationApplication, prompt: str) -> None:
    print("assistant> ", end="", flush=True)
    async with application.stream_turn(prompt) as stream:
        async for text in stream:
            print(text, end="", flush=True)
    print()


async def _run_conversation(arguments: argparse.Namespace) -> None:
    state_path = arguments.state.resolve()
    workspace = arguments.workspace.resolve() if arguments.workspace is not None else state_path.parent / "workspace"
    application = ConversationApplication(
        model=create_demo_model(),
        state_path=state_path,
        environment=create_demo_environment(workspace),
    )
    async with application:
        if arguments.prompts:
            for prompt in arguments.prompts:
                print(f"you> {prompt}")
                await _print_turn(application, prompt)
            return

        print("Enter a message, or type /quit to stop.")
        while True:
            try:
                prompt = await asyncio.to_thread(input, "you> ")
            except EOFError:
                break
            if prompt.strip() == "/quit":
                break
            if not prompt.strip():
                continue
            await _print_turn(application, prompt)


async def _run(arguments: argparse.Namespace) -> None:
    await _run_conversation(arguments)


def _create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompts",
        nargs="*",
        help="Optional turns to run non-interactively; omit them for an interactive conversation",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=_DEFAULT_STATE_PATH,
        help=f"Harness state file (default: {_DEFAULT_STATE_PATH})",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Demo Environment workspace (default: a workspace directory next to the state file)",
    )
    return parser


def main() -> None:
    asyncio.run(_run(_create_argument_parser().parse_args()))


if __name__ == "__main__":
    main()
